"""Concept + keyword tagging from the root repository's README.

Optional pipeline stage. Stamps two underscore-prefixed metadata fields onto
the root repository entity:

- ``_keywords`` — list[str] of plain keyword tokens.
- ``_concepts`` — list[dict] of structured concepts, each shaped::

      {
          "label": str,
          "wikipedia_url": str | None,
          "wikidata_id": str | None,
          "score": float | None,
          "source": str,   # backend that emitted this concept
      }

Both fields are stripped before strict validation (handled by
``schema_validation.py``: any ``_``-prefixed key is dropped) and before
JSON-LD build (handled by ``jsonld_build.HELPER_ONLY_FIELDS``). They are
purely internal pipeline metadata for now — surface them in the JSON-LD
output by adding the corresponding ontology terms to the strict schema.

Backends are pluggable via ``V2_CONCEPT_TAGGING_BACKEND``:

- ``epfl_graph`` (default) — calls the EPFL Graph (graphai) API via
  ``src.module.epfl_graph``. Requires ``EPFL_GRAPH_USERNAME`` /
  ``EPFL_GRAPH_PASSWORD``.
- ``wikipedia`` — deterministic, credential-free. Extracts candidate
  phrases from the README with a simple heuristic and resolves each to a
  Wikipedia page via the MediaWiki opensearch API.
- ``llm`` — pydantic-ai agent that emits ``{keywords, concepts}`` JSON.
  Reuses the project's ``model_config`` plumbing (``RCP_TOKEN`` /
  ``OPENAI_API_KEY`` / ``OPENROUTER_API_KEY``).

The whole stage is opt-in via ``V2_CONCEPT_TAGGING_ENABLED=true`` because
each backend has its own external dependency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests

logger = logging.getLogger(__name__)

BACKEND_EPFL_GRAPH = "epfl_graph"
BACKEND_WIKIPEDIA = "wikipedia"
BACKEND_LLM = "llm"
SUPPORTED_BACKENDS = (BACKEND_EPFL_GRAPH, BACKEND_WIKIPEDIA, BACKEND_LLM)
DEFAULT_BACKEND = BACKEND_EPFL_GRAPH

DEFAULT_MAX_CONCEPTS = 25
DEFAULT_MAX_KEYWORDS = 25
DEFAULT_MAX_DISCIPLINES = 10
DEFAULT_README_CHARS = 8000
DEFAULT_EPFL_MIN_SCORE = 0.0
DEFAULT_DISCIPLINE_TOP_CONCEPTS = 6  # how many concepts to look up categories for
DEFAULT_DISCIPLINE_TOP_N_PER_CONCEPT = 3
DEFAULT_RELATED_TOP_DISCIPLINES = 3   # enrich the top-N disciplines, not all
DEFAULT_RELATED_TOP_N = 5             # entities per type per discipline
DEFAULT_RELATED_TOP_TOPICS = 3        # OpenAlex topics per discipline

CONCEPTS_FIELD = "_concepts"
KEYWORDS_FIELD = "_keywords"
DISCIPLINES_FIELD = "_disciplines"


@dataclass(slots=True)
class ConceptTaggingResult:
    keywords: list[str] = field(default_factory=list)
    concepts: list[dict[str, Any]] = field(default_factory=list)
    disciplines: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    backend: str = ""


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cutoff = text.rfind(" ", 0, max_chars)
    return text[: cutoff if cutoff > max_chars * 0.6 else max_chars]


def _dedupe_keywords(keywords: Iterable[str], limit: int) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for raw in keywords:
        if not isinstance(raw, str):
            continue
        clean = raw.strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clean)
        if len(deduped) >= limit:
            break
    return deduped


def _dedupe_concepts(
    concepts: Iterable[dict[str, Any]], limit: int,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        url = concept.get("wikipedia_url")
        label = concept.get("label")
        key = (url or label or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(concept)
        if len(deduped) >= limit:
            break
    return deduped


# Backend: EPFL Graph (graphai) ----------------------------------------------


def _epfl_concept_to_dict(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    label = (
        item.get("concept_name")
        or item.get("PageTitle")
        or item.get("page_title")
        or item.get("title")
        or item.get("label")
    )
    if not isinstance(label, str) or not label.strip():
        return None
    wikipedia_url = item.get("WikipediaURL") or item.get("wikipedia_url") or None
    if not wikipedia_url and label:
        wikipedia_url = (
            "https://en.wikipedia.org/wiki/" + label.strip().replace(" ", "_")
        )
    # `concept_id` from EPFL Graph is the Wikipedia page ID, not a Wikidata
    # QID; keep it as a typed reference so callers can disambiguate later.
    raw_concept_id = item.get("concept_id")
    concept_id: str | None = None
    if isinstance(raw_concept_id, (int, str)) and str(raw_concept_id).strip():
        concept_id = str(raw_concept_id).strip()
    wikidata_id = item.get("WikidataID") or item.get("wikidata_id") or None
    raw_score = (
        item.get("mixed_score")
        or item.get("MixedScore")
        or item.get("Score")
        or item.get("score")
    )
    try:
        score = float(raw_score) if raw_score is not None else None
    except (TypeError, ValueError):
        score = None
    return {
        "label": label.strip(),
        "wikipedia_url": wikipedia_url,
        "wikidata_id": wikidata_id,
        "concept_id": concept_id,
        "score": score,
        "source": BACKEND_EPFL_GRAPH,
    }


def _extract_via_epfl_graph(  # noqa: PLR0913, C901, PLR0912, PLR0915
    readme: str,
    *,
    max_concepts: int,
    max_keywords: int,
    min_score: float = DEFAULT_EPFL_MIN_SCORE,
    enrich_with_disciplines: bool = True,
    max_disciplines: int = DEFAULT_MAX_DISCIPLINES,
    discipline_top_concepts: int = DEFAULT_DISCIPLINE_TOP_CONCEPTS,
    discipline_top_n: int = DEFAULT_DISCIPLINE_TOP_N_PER_CONCEPT,
) -> ConceptTaggingResult:
    result = ConceptTaggingResult(backend=BACKEND_EPFL_GRAPH)
    try:
        from src.module.epfl_graph import (  # noqa: PLC0415
            category_chain,
            category_graphsearch_url,
            category_wikipedia,
            concept_nearest_categories,
            extract_concepts_from_text,
            extract_keywords_from_text,
        )
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"epfl_graph backend unavailable: {exc}")
        return result

    cleaned = _strip_markdown(readme).strip()
    if not cleaned:
        result.warnings.append("epfl_graph: empty text after markdown stripping")
        return result

    try:
        raw_keywords = extract_keywords_from_text(cleaned) or []
    except Exception as exc:  # noqa: BLE001
        raw_keywords = []
        result.warnings.append(f"epfl_graph keyword extraction failed: {exc}")

    try:
        raw_concepts = extract_concepts_from_text(cleaned) or []
    except Exception as exc:  # noqa: BLE001
        raw_concepts = []
        result.warnings.append(f"epfl_graph concept extraction failed: {exc}")

    result.keywords = _dedupe_keywords(raw_keywords, max_keywords)
    converted: list[dict[str, Any]] = []
    for raw in raw_concepts:
        concept = _epfl_concept_to_dict(raw)
        if concept is None:
            continue
        score = concept.get("score")
        if isinstance(score, (int, float)) and score < min_score:
            continue
        converted.append(concept)
    result.concepts = _dedupe_concepts(converted, max_concepts)

    if enrich_with_disciplines and result.concepts:
        seen: set[str] = set()
        disciplines: list[dict[str, Any]] = []
        for concept in result.concepts[:discipline_top_concepts]:
            concept_id = concept.get("concept_id")
            if not concept_id:
                continue
            try:
                categories = concept_nearest_categories(
                    concept_id, top_n=discipline_top_n,
                )
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(
                    f"epfl_graph: discipline lookup failed for "
                    f"{concept.get('label', concept_id)}: {exc}",
                )
                continue
            for category in categories:
                if not isinstance(category, dict):
                    continue
                category_id = category.get("category_id")
                if not isinstance(category_id, str) or not category_id:
                    continue
                if category_id in seen:
                    continue
                seen.add(category_id)
                chain_ids = category_chain(category_id)
                chain = []
                for idx, cid in enumerate(chain_ids):
                    wiki = category_wikipedia(cid)
                    chain.append(
                        {
                            "category_id": cid,
                            "label": wiki.get("name"),
                            "depth": idx,  # 0 = leaf, increases toward root
                            "wikipedia_url": wiki.get("wikipedia_url"),
                            "wikipedia_page_id": wiki.get("wikipedia_page_id"),
                        },
                    )
                leaf_wiki = chain[0] if chain else {}
                disciplines.append(
                    {
                        "category_id": category_id,
                        "label": leaf_wiki.get("label") or category_id,
                        "score": category.get("score"),
                        "rank": category.get("rank"),
                        "from_concept": concept.get("label"),
                        "source": BACKEND_EPFL_GRAPH,
                        "graphsearch_url": category_graphsearch_url(category_id),
                        "wikipedia_url": leaf_wiki.get("wikipedia_url"),
                        "wikipedia_page_id": leaf_wiki.get("wikipedia_page_id"),
                        "chain": chain,
                    },
                )
                if len(disciplines) >= max_disciplines:
                    break
            if len(disciplines) >= max_disciplines:
                break
        # Sort by score desc, then rank asc — EPFL Graph scores are not
        # bounded, so a simple max() across concepts already orders well.
        disciplines.sort(
            key=lambda item: (
                -(item.get("score") or 0.0),
                item.get("rank") or 0,
            ),
        )
        result.disciplines = disciplines
    return result


# Backend: Wikipedia (rule-based, credential-free) ---------------------------

_STOPWORDS = frozenset(
    {
        "the", "a", "an", "of", "in", "and", "or", "to", "for", "on", "at",
        "by", "with", "from", "as", "is", "are", "was", "were", "be", "been",
        "being", "this", "that", "these", "those", "it", "its", "we", "you",
        "they", "their", "our", "your", "his", "her", "him", "she", "he",
        "but", "not", "no", "yes", "if", "then", "else", "so", "than", "such",
        "can", "could", "will", "would", "shall", "should", "may", "might",
        "must", "do", "does", "did", "have", "has", "had", "into", "out",
        "over", "under", "about", "after", "before", "while", "when", "where",
        "what", "which", "who", "whom", "whose", "how", "why", "any", "all",
        "some", "each", "every", "both", "few", "more", "most", "other",
        "another", "same", "different", "via", "use", "used", "using", "uses",
        "see", "also", "etc", "eg", "ie", "vs",
    },
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]{2,}")
_CODE_FENCE_RE = re.compile(r"```.*?```", flags=re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ATX_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s+", flags=re.MULTILINE)
_SETEXT_HEADER_RE = re.compile(r"^[=\-]{3,}\s*$", flags=re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)")
_BULLET_RE = re.compile(r"^\s*([*+\-]|\d+\.)\s+", flags=re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?", flags=re.MULTILINE)
_HORIZONTAL_RULE_RE = re.compile(r"^\s*(?:[*\-_]\s*){3,}\s*$", flags=re.MULTILINE)


def _strip_markdown(text: str) -> str:
    text = _CODE_FENCE_RE.sub(" ", text)
    text = _IMAGE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _ATX_HEADER_RE.sub("", text)
    text = _SETEXT_HEADER_RE.sub("", text)
    text = _HORIZONTAL_RULE_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _BLOCKQUOTE_RE.sub("", text)
    text = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    return _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)


def _candidate_keywords_from_text(text: str, limit: int) -> list[str]:
    """Cheap keyword surrogate: top frequent non-stopword tokens.

    Not a substitute for a proper keyword extractor — just enough to seed
    Wikipedia lookups when no smarter backend is available.
    """
    cleaned = _strip_markdown(text)
    tokens = _WORD_RE.findall(cleaned)
    counts: Counter[str] = Counter()
    for token in tokens:
        lowered = token.lower()
        if lowered in _STOPWORDS:
            continue
        counts[lowered] += 1
    most_common = [token for token, _ in counts.most_common(limit * 4)]
    return most_common[:limit]


def _wikipedia_lookup(
    keyword: str, *, session: requests.Session, timeout: float = 10.0,
) -> dict[str, Any] | None:
    try:
        response = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": keyword,
                "srlimit": "1",
                "srprop": "",
            },
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    hits = payload.get("query", {}).get("search", []) if isinstance(payload, dict) else []
    if not isinstance(hits, list) or not hits:
        return None
    first = hits[0]
    if not isinstance(first, dict):
        return None
    title = first.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    title = title.strip()
    return {
        "label": title,
        "wikipedia_url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
        "wikidata_id": None,
        "score": None,
        "source": BACKEND_WIKIPEDIA,
    }


def _extract_via_wikipedia(
    readme: str,
    *,
    max_concepts: int,
    max_keywords: int,
) -> ConceptTaggingResult:
    result = ConceptTaggingResult(backend=BACKEND_WIKIPEDIA)
    keywords = _candidate_keywords_from_text(readme, limit=max_keywords)
    result.keywords = keywords

    concepts: list[dict[str, Any]] = []
    with requests.Session() as session:
        session.headers.update(
            {"User-Agent": "git-metadata-extractor/2.0 (+concept_tagging)"},
        )
        for keyword in keywords[:max_concepts]:
            concept = _wikipedia_lookup(keyword, session=session)
            if concept is not None:
                concepts.append(concept)
    result.concepts = _dedupe_concepts(concepts, max_concepts)
    return result


# Backend: LLM (pydantic-ai, project model config) ---------------------------


_LLM_SYSTEM_PROMPT = (
    "You extract concepts and keywords from a software repository README.\n"
    "Return ONLY a JSON object matching this schema (no prose, no fences):\n"
    "{\n"
    '  "keywords": ["..."],          // 5-25 short tokens, lowercase preferred\n'
    '  "concepts": [                 // 5-25 entries\n'
    '    {"label": "...", "wikipedia_url": "https://en.wikipedia.org/wiki/..."}\n'
    "  ]\n"
    "}\n"
    "Rules:\n"
    "- Only use Wikipedia URLs you are confident exist.\n"
    "- Prefer canonical English Wikipedia pages.\n"
    "- Skip generic terms (software, project, code, README, version)."
)


def _llm_payload_to_result(payload: Any) -> ConceptTaggingResult:
    result = ConceptTaggingResult(backend=BACKEND_LLM)
    if not isinstance(payload, dict):
        result.warnings.append("llm backend returned a non-object payload")
        return result
    raw_keywords = payload.get("keywords") or []
    raw_concepts = payload.get("concepts") or []
    if isinstance(raw_keywords, list):
        result.keywords = _dedupe_keywords(raw_keywords, DEFAULT_MAX_KEYWORDS)
    converted: list[dict[str, Any]] = []
    if isinstance(raw_concepts, list):
        for item in raw_concepts:
            if not isinstance(item, dict):
                continue
            label = item.get("label") or item.get("title")
            if not isinstance(label, str) or not label.strip():
                continue
            url = item.get("wikipedia_url") or item.get("url")
            converted.append(
                {
                    "label": label.strip(),
                    "wikipedia_url": url if isinstance(url, str) and url else None,
                    "wikidata_id": None,
                    "score": None,
                    "source": BACKEND_LLM,
                },
            )
    result.concepts = _dedupe_concepts(converted, DEFAULT_MAX_CONCEPTS)
    return result


async def _extract_via_llm(
    readme: str,
    *,
    max_concepts: int,
    max_keywords: int,
) -> ConceptTaggingResult:
    try:
        from pydantic_ai import Agent  # noqa: PLC0415

        from src.v1.llm.model_config import (  # noqa: PLC0415
            create_pydantic_ai_model,
            get_model_parameters,
            load_model_config,
            validate_config,
        )
    except Exception as exc:  # noqa: BLE001
        return ConceptTaggingResult(
            backend=BACKEND_LLM,
            warnings=[f"llm backend unavailable: {exc}"],
        )

    try:
        config = load_model_config()
        validate_config(config)
        model = create_pydantic_ai_model(config)
        params = get_model_parameters(config)
    except Exception as exc:  # noqa: BLE001
        return ConceptTaggingResult(
            backend=BACKEND_LLM,
            warnings=[f"llm backend config invalid: {exc}"],
        )

    agent: Agent[None, dict[str, Any]] = Agent(
        model=model,
        output_type=dict,
        system_prompt=_LLM_SYSTEM_PROMPT,
        model_settings=params or None,
    )
    user_prompt = (
        f"Extract up to {max_keywords} keywords and up to {max_concepts} "
        "concepts from this README:\n\n" + readme
    )
    try:
        run = await agent.run(user_prompt)
    except Exception as exc:  # noqa: BLE001
        return ConceptTaggingResult(
            backend=BACKEND_LLM,
            warnings=[f"llm backend call failed: {exc}"],
        )

    payload = getattr(run, "output", None)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return ConceptTaggingResult(
                backend=BACKEND_LLM,
                warnings=["llm backend returned non-JSON string"],
            )
    return _llm_payload_to_result(payload)


# Stage entrypoint ------------------------------------------------------------


def _enrich_disciplines_with_related(
    disciplines: list[dict[str, Any]],
    *,
    top_disciplines: int,
    top_topics: int,
    top_n: int,
    warnings: list[str],
) -> None:
    """Stamp ``openalex_topics`` / ``publications`` / ``people`` / ``units``
    onto the top disciplines in-place via the OpenAlex API.

    Opt-in (gated by ``V2_CONCEPT_TAGGING_OPENALEX_RELATED_ENABLED``)
    because each enabled discipline triggers ~4 OpenAlex calls — cheap
    individually but worth keeping off the default path.
    """
    if not disciplines:
        return

    try:
        from src.module.epfl_graph import (  # noqa: PLC0415
            category_nearest_openalex_topics,
            people_for_topics,
            publications_for_topics,
            units_for_topics,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"concept_tagging: enrichment imports failed: {exc}")
        return

    for discipline in disciplines[:top_disciplines]:
        category_id = discipline.get("category_id")
        if not isinstance(category_id, str) or not category_id:
            continue

        try:
            topics = category_nearest_openalex_topics(
                category_id, top_n=top_topics,
            )
        except Exception as exc:  # noqa: BLE001
            topics = []
            warnings.append(
                f"concept_tagging: openalex topic lookup failed for "
                f"{category_id}: {exc}",
            )
        if not topics:
            continue
        discipline["openalex_topics"] = topics
        topic_ids = [t["topic_id"] for t in topics if t.get("topic_id")]
        if not topic_ids:
            continue
        try:
            discipline["publications"] = publications_for_topics(
                topic_ids, top_n=top_n,
            )
            discipline["people"] = people_for_topics(topic_ids, top_n=top_n)
            discipline["units"] = units_for_topics(topic_ids, top_n=top_n)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"concept_tagging: openalex enrichment failed for "
                f"{category_id}: {exc}",
            )


async def run_concept_tagging_stage(  # noqa: PLR0913
    *,
    root_entity: dict[str, Any] | None,
    readme_text: str | None,
    backend: str = DEFAULT_BACKEND,
    max_concepts: int = DEFAULT_MAX_CONCEPTS,
    max_keywords: int = DEFAULT_MAX_KEYWORDS,
    max_readme_chars: int = DEFAULT_README_CHARS,
    epfl_min_score: float = DEFAULT_EPFL_MIN_SCORE,
    enrich_with_disciplines: bool = True,
    enable_related_openalex: bool = False,
    related_top_disciplines: int = DEFAULT_RELATED_TOP_DISCIPLINES,
    related_top_topics: int = DEFAULT_RELATED_TOP_TOPICS,
    related_top_n: int = DEFAULT_RELATED_TOP_N,
) -> tuple[dict[str, Any] | None, ConceptTaggingResult]:
    """Stamp ``_concepts`` and ``_keywords`` onto the root repository entity.

    Returns the (possibly mutated) root entity and the raw extraction
    result. The function is a no-op (returns the entity untouched) when
    ``root_entity`` is missing, when ``readme_text`` is empty, or when the
    selected backend produces nothing.
    """
    backend_normalized = (backend or DEFAULT_BACKEND).strip().lower()
    result = ConceptTaggingResult(backend=backend_normalized)

    if backend_normalized not in SUPPORTED_BACKENDS:
        result.warnings.append(
            f"concept_tagging: unknown backend '{backend_normalized}', "
            f"expected one of {SUPPORTED_BACKENDS}",
        )
        return root_entity, result

    if root_entity is None or not isinstance(root_entity, dict):
        result.warnings.append("concept_tagging: no root entity to tag")
        return root_entity, result

    if not isinstance(readme_text, str) or not readme_text.strip():
        result.warnings.append("concept_tagging: empty README, nothing to tag")
        return root_entity, result

    truncated = _truncate(readme_text.strip(), max_readme_chars)

    if backend_normalized == BACKEND_EPFL_GRAPH:
        result = await asyncio.to_thread(
            _extract_via_epfl_graph,
            truncated,
            max_concepts=max_concepts,
            max_keywords=max_keywords,
            min_score=epfl_min_score,
            enrich_with_disciplines=enrich_with_disciplines,
        )
    elif backend_normalized == BACKEND_WIKIPEDIA:
        result = await asyncio.to_thread(
            _extract_via_wikipedia,
            truncated,
            max_concepts=max_concepts,
            max_keywords=max_keywords,
        )
    else:  # llm
        result = await _extract_via_llm(
            truncated,
            max_concepts=max_concepts,
            max_keywords=max_keywords,
        )

    if result.disciplines and enable_related_openalex:
        await asyncio.to_thread(
            _enrich_disciplines_with_related,
            result.disciplines,
            top_disciplines=related_top_disciplines,
            top_topics=related_top_topics,
            top_n=related_top_n,
            warnings=result.warnings,
        )

    if result.keywords:
        root_entity[KEYWORDS_FIELD] = result.keywords
    if result.concepts:
        root_entity[CONCEPTS_FIELD] = result.concepts
    if result.disciplines:
        root_entity[DISCIPLINES_FIELD] = result.disciplines

    return root_entity, result


def is_enabled() -> bool:
    """Read the opt-in env flag (default off)."""
    raw = os.environ.get("V2_CONCEPT_TAGGING_ENABLED", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def resolve_epfl_min_score() -> float:
    """Read ``V2_CONCEPT_TAGGING_EPFL_MIN_SCORE`` (default 0.0).

    Concepts emitted by the EPFL Graph below this ``mixed_score`` threshold
    are dropped before tagging. Useful to filter out generic noise (the API
    happily returns matches like "Graph theory" or "OS/2" with low scores
    on README text that mentions "graph" or "operating system" in passing).
    """
    raw = os.environ.get("V2_CONCEPT_TAGGING_EPFL_MIN_SCORE", "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_EPFL_MIN_SCORE


def resolve_related_enrichment() -> bool:
    """Read ``V2_CONCEPT_TAGGING_OPENALEX_RELATED_ENABLED`` (default false).

    When true, top disciplines are enriched with related OpenAlex topics,
    publications, people, and institutions (~4 cheap HTTP per discipline).
    """
    truthy = {"1", "true", "yes", "on"}
    raw = (
        os.environ.get("V2_CONCEPT_TAGGING_OPENALEX_RELATED_ENABLED", "")
        .strip()
        .lower()
    )
    return raw in truthy


def resolve_backend() -> str:
    raw = os.environ.get("V2_CONCEPT_TAGGING_BACKEND", DEFAULT_BACKEND)
    backend = (raw or DEFAULT_BACKEND).strip().lower()
    if backend not in SUPPORTED_BACKENDS:
        logger.warning(
            "V2_CONCEPT_TAGGING_BACKEND=%s not recognized; falling back to %s",
            raw,
            DEFAULT_BACKEND,
        )
        return DEFAULT_BACKEND
    return backend
