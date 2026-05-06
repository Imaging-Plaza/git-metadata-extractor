"""Build/embed stage: chunk, embed, and populate ChromaDB collections.

Scopes:
    * `chunks`: text/<uuid>.txt → chunks → embeddings → ethz_research_collection_chunks
    * `articles`: raw/items/<uuid>.json + matches → ethz_research_collection_articles
    * `persons`: raw/persons/<uuid>.json → ethz_research_collection_persons
    * `orgs`: raw/organizations/<uuid>.json → ethz_research_collection_organizations
    * `all`: every scope above, in order

Each entity row gets an embedding when there is non-trivial source text;
otherwise a zero-vector placeholder is written so the row is still
queryable via metadata/lexical filters (vector search effectively
ignores it via the cosine score).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .chunker import chunk_text
from .config import EthzResearchCollectionIndexConfig
from .embed import RCPEmbedder
from .extract_matches import matches_by_uuid
from .extract_relations import load_relations
from .models import (
    ArticleRecord,
    ChunkRecord,
    OrganizationRecord,
    PersonRecord,
)
from .parsers import parse_article, parse_organization, parse_person
from .paths import (
    raw_items_dir,
    raw_organizations_dir,
    raw_persons_dir,
    text_dir,
)
from .store import (
    ARTICLES_COLLECTION,
    CHUNKS_COLLECTION,
    ORGANIZATIONS_COLLECTION,
    PERSONS_COLLECTION,
    QdrantStore,
    upsert_articles,
    upsert_chunks,
    upsert_organizations,
    upsert_persons,
)

logger = logging.getLogger(__name__)


def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to parse %s", path)
        return None


def _chunks_for_article(
    cfg: EthzResearchCollectionIndexConfig,
    article: ArticleRecord,
    text: str,
) -> List[ChunkRecord]:
    pieces = chunk_text(text, cfg.chunking)
    out: List[ChunkRecord] = []
    for i, piece in enumerate(pieces):
        out.append(ChunkRecord(
            chunk_id=f"{article.article_uuid}::{i}",
            article_uuid=article.article_uuid,
            chunk_index=i,
            text=piece,
            title=article.title,
            abstract=article.abstract,
            authors=article.authors,
            author_uuids=article.author_uuids,
            doi=article.doi,
            publication_date=article.publication_date,
            year=article.year,
            publication_type=article.publication_type,
            language=article.language,
            subjects=article.subjects,
            keywords=article.keywords,
            lab=article.lab,
            lab_uuid=article.lab_uuid,
            org_uuids=article.org_uuids,
            research_collection_url=article.research_collection_url,
            matched_urls=article.matched_urls,
        ))
    return out


def _articles_with_matches(matches_map: dict) -> List[ArticleRecord]:
    out: List[ArticleRecord] = []
    for uuid, match in matches_map.items():
        item = _load_json(raw_items_dir() / f"{uuid}.json")
        if item is None:
            continue
        out.append(parse_article(item, matched_urls=match.matched_urls))
    return out


def _build_article_to_persons() -> Dict[str, List[str]]:
    rev: Dict[str, List[str]] = defaultdict(list)
    for rel in load_relations():
        for p in rel.person_uuids:
            rev[p].append(rel.article_uuid)
    return rev


def _build_article_to_orgs() -> Dict[str, List[str]]:
    rev: Dict[str, List[str]] = defaultdict(list)
    for rel in load_relations():
        for o in rel.org_uuids:
            rev[o].append(rel.article_uuid)
    return rev


def _person_text(rec: PersonRecord) -> str:
    """Build the embedding text for a Person record.

    Ordering matters for retrieval: surface every form of the person's
    name first (display name, given+family, family alone) so direct-name
    queries score highly, then add the affiliation/department text and
    biography for content-based recall.
    """
    parts: List[str] = []
    if rec.name:
        parts.append(rec.name)
    # Repeat given+family on a separate line — Qwen3-Embedding treats line
    # boundaries as soft topic shifts; this gives the family-name token an
    # extra anchor for queries that pass just "Reiher" or "Markus Reiher".
    if rec.given_name or rec.family_name:
        composed = " ".join(p for p in (rec.given_name, rec.family_name) if p)
        if composed and composed != rec.name:
            parts.append(composed)
    if rec.family_name and rec.family_name != rec.name:
        parts.append(rec.family_name)
    if rec.orcid:
        parts.append(f"ORCID: {rec.orcid}")
    if rec.position:
        parts.append(rec.position)
    if rec.primary_affiliation:
        # ETH RC pumps "<leitzahl> - <lab name> / <head>" through this
        # field — useful for queries that name a lab / professor.
        parts.append(rec.primary_affiliation)
    if rec.research_interests:
        parts.append(", ".join(rec.research_interests))
    if rec.biography:
        parts.append(rec.biography)
    return "\n\n".join(parts).strip()


def _organization_text(rec: OrganizationRecord) -> str:
    parts: List[str] = []
    if rec.name:
        header = rec.name
        if rec.acronym:
            header = f"{rec.name} ({rec.acronym})"
        parts.append(header)
    if rec.parent_org_chain_names:
        parts.append("Parent: " + " > ".join(rec.parent_org_chain_names))
    if rec.description:
        parts.append(rec.description)
    return "\n\n".join(parts).strip()


def _article_embed_text(rec: ArticleRecord) -> str:
    parts: List[str] = []
    if rec.title:
        parts.append(rec.title)
    if rec.abstract:
        parts.append(rec.abstract)
    return "\n\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Stage entry points
# ---------------------------------------------------------------------------


# Slab size for incremental embed → upsert. Each slab embeds N chunks
# (one RCP /embeddings batch group), upserts them to Qdrant, drops them,
# then moves on. Cuts peak memory from O(total_chunks × 16 KB) — 6+ GB on
# the full ETH RC run — to O(slab × 16 KB) ≈ 8 MB. Also makes the run
# resumable: if Qdrant already has a chunk's UUIDv5 point id, the upsert
# is a no-op so re-running embed picks up where it left off without
# re-embedding the slabs that already landed.
_CHUNK_SLAB_SIZE = 512


async def build_chunks(cfg: EthzResearchCollectionIndexConfig) -> dict:
    matches = matches_by_uuid()
    articles = _articles_with_matches(matches)
    if not articles:
        logger.warning("No matched articles. Run discover/fetch-text/extract-matches first.")
        return {"chunks": 0, "articles": 0}

    chunk_records: List[ChunkRecord] = []
    chunk_counts_per_article: Dict[str, int] = {}
    for article in articles:
        text_path = text_dir() / f"{article.article_uuid}.txt"
        text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""
        recs = _chunks_for_article(cfg, article, text)
        chunk_counts_per_article[article.article_uuid] = len(recs)
        chunk_records.extend(recs)

    if not chunk_records:
        logger.warning("Articles have no extractable chunk text yet.")
        return {"chunks": 0, "articles": len(articles)}

    store = QdrantStore.from_config(cfg)
    store.ensure_collection(CHUNKS_COLLECTION)

    total = len(chunk_records)
    upserted = 0
    slab_size = _CHUNK_SLAB_SIZE
    async with RCPEmbedder(cfg.rcp) as embedder:
        for start in range(0, total, slab_size):
            slab = chunk_records[start : start + slab_size]
            texts = [c.text for c in slab]
            embeddings = await embedder.embed_passages_batched(texts)
            upsert_chunks(store, slab, embeddings)
            upserted += len(slab)
            logger.info(
                "build_chunks: %d / %d chunks upserted (slab %d/%d)",
                upserted, total,
                (start // slab_size) + 1,
                (total + slab_size - 1) // slab_size,
            )

    return {
        "chunks": total,
        "articles": len(articles),
        "chunk_counts_per_article": chunk_counts_per_article,
    }


async def build_articles(cfg: EthzResearchCollectionIndexConfig) -> dict:
    matches = matches_by_uuid()
    articles = _articles_with_matches(matches)
    if not articles:
        return {"articles": 0}

    # Derive chunk_count per article from the on-disk text files (no
    # round-trip to Qdrant needed; chunker is deterministic so the count
    # we'd insert in build_chunks matches what we'd see in the store).
    chunk_counts: Dict[str, int] = {}
    for art in articles:
        text_path = text_dir() / f"{art.article_uuid}.txt"
        if not text_path.exists():
            continue
        body = text_path.read_text(encoding="utf-8")
        chunk_counts[art.article_uuid] = len(chunk_text(body, cfg.chunking))

    for art in articles:
        art.chunk_count = int(chunk_counts.get(art.article_uuid, 0))

    store = QdrantStore.from_config(cfg)
    store.ensure_collection(ARTICLES_COLLECTION)

    texts = [_article_embed_text(a) for a in articles]
    have_text = [bool(t) for t in texts]
    to_embed = [t for t, ok in zip(texts, have_text) if ok]

    if to_embed:
        async with RCPEmbedder(cfg.rcp) as embedder:
            embeddings_iter = await embedder.embed_passages_batched(to_embed)
    else:
        embeddings_iter = []

    embeddings: List[Optional[Sequence[float]]] = []
    embed_pos = 0
    for ok in have_text:
        if ok:
            embeddings.append(embeddings_iter[embed_pos])
            embed_pos += 1
        else:
            embeddings.append(None)

    upsert_articles(store, articles, embeddings, cfg.rcp.embedding_dim)
    return {"articles": len(articles), "with_embedding": len(to_embed)}


async def build_persons(cfg: EthzResearchCollectionIndexConfig) -> dict:
    files = sorted(raw_persons_dir().glob("*.json"))
    if not files:
        return {"persons": 0}

    rev = _build_article_to_persons()
    records: List[PersonRecord] = []
    for f in files:
        item = _load_json(f)
        if item is None:
            continue
        rec = parse_person(item)
        rec.related_article_uuids = rev.get(rec.person_uuid, [])
        records.append(rec)

    store = QdrantStore.from_config(cfg)
    store.ensure_collection(PERSONS_COLLECTION)

    texts = [_person_text(r) for r in records]
    have_text = [bool(t) for t in texts]
    to_embed = [t for t, ok in zip(texts, have_text) if ok]

    if to_embed:
        async with RCPEmbedder(cfg.rcp) as embedder:
            embeddings_iter = await embedder.embed_passages_batched(to_embed)
    else:
        embeddings_iter = []

    embeddings: List[Optional[Sequence[float]]] = []
    embed_pos = 0
    for ok in have_text:
        if ok:
            embeddings.append(embeddings_iter[embed_pos])
            embed_pos += 1
        else:
            embeddings.append(None)

    upsert_persons(store, records, embeddings, cfg.rcp.embedding_dim)
    return {"persons": len(records), "with_embedding": len(to_embed)}


async def build_organizations(cfg: EthzResearchCollectionIndexConfig) -> dict:
    files = sorted(raw_organizations_dir().glob("*.json"))
    if not files:
        return {"organizations": 0}

    rev = _build_article_to_orgs()
    records: List[OrganizationRecord] = []
    for f in files:
        item = _load_json(f)
        if item is None:
            continue
        rec = parse_organization(item)
        rec.related_article_uuids = rev.get(rec.org_uuid, [])
        records.append(rec)

    store = QdrantStore.from_config(cfg)
    store.ensure_collection(ORGANIZATIONS_COLLECTION)

    texts = [_organization_text(r) for r in records]
    have_text = [bool(t) for t in texts]
    to_embed = [t for t, ok in zip(texts, have_text) if ok]

    if to_embed:
        async with RCPEmbedder(cfg.rcp) as embedder:
            embeddings_iter = await embedder.embed_passages_batched(to_embed)
    else:
        embeddings_iter = []

    embeddings: List[Optional[Sequence[float]]] = []
    embed_pos = 0
    for ok in have_text:
        if ok:
            embeddings.append(embeddings_iter[embed_pos])
            embed_pos += 1
        else:
            embeddings.append(None)

    upsert_organizations(store, records, embeddings, cfg.rcp.embedding_dim)
    return {"organizations": len(records), "with_embedding": len(to_embed)}


async def build(cfg: EthzResearchCollectionIndexConfig, *, scope: str = "all") -> dict:
    summary: dict = {"scope": scope}
    if scope in ("chunks", "all"):
        summary["chunks"] = await build_chunks(cfg)
    if scope in ("articles", "all"):
        summary["articles"] = await build_articles(cfg)
    if scope in ("persons", "all"):
        summary["persons"] = await build_persons(cfg)
    if scope in ("orgs", "all"):
        summary["organizations"] = await build_organizations(cfg)
    logger.info("build: %s", json.dumps(summary, default=str))
    return summary


def run(cfg: EthzResearchCollectionIndexConfig, *, scope: str = "all") -> dict:
    return asyncio.run(build(cfg, scope=scope))
