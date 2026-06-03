"""Project Zenodo record JSON → DuckDB rows and persist."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time as _time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from bs4 import BeautifulSoup

from src.index.zenodo_records.ingest.scope import Scope
from src.index.zenodo_records.ingest.zenodo_client import ZenodoClient

if TYPE_CHECKING:
    from src.index.zenodo_records.config import ZenodoIndexConfig
    from src.index.zenodo_records.storage.duckdb_store import ZenodoRecordsStore

LOGGER = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


_STRIP_MAX_PASSES = 5


def _strip_html(raw: str | None) -> str | None:
    """Strip HTML to plain text, robust against multi-level escaping.

    Zenodo wraps record `metadata.description` in a `<p>` element and
    then HTML-escapes the inner content (`&lt;div&gt;…`). One pass of
    `BeautifulSoup.get_text()` unescapes entities while extracting
    text — which removes the `<p>` shell but turns the formerly-escaped
    inner string back into a fresh HTML document that still needs
    stripping. Loop until the output stabilises (or we hit
    `_STRIP_MAX_PASSES`, which bounds runaway on pathological input).

    Early-exits when the working text has no remaining `<` or `>` so
    the common path (already-clean text) makes a single pass.
    """
    if not raw or not raw.strip():
        return None
    text = raw
    for _ in range(_STRIP_MAX_PASSES):
        if "<" not in text and ">" not in text:
            break
        stripped = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
        if stripped == text:
            break
        text = stripped
    text = text.strip()
    return text or None


def _parse_publication_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = _DATE_RE.match(raw.strip())
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2) or 1)
    day = int(m.group(3) or 1)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _slugify_creator_key(name: str | None, orcid: str | None) -> str | None:
    if orcid:
        # Normalize to canonical https://orcid.org/<id> form.
        orcid_clean = orcid.strip()
        if orcid_clean.startswith("http"):
            return orcid_clean
        return f"https://orcid.org/{orcid_clean}"
    if not name:
        return None
    slug = _NON_WORD_RE.sub("-", name.lower()).strip("-")
    return f"name:{slug}" if slug else None


def _project_record(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    license_block = metadata.get("license") or {}
    if isinstance(license_block, dict):
        license_id = license_block.get("id") or license_block.get("identifier")
    else:
        license_id = str(license_block) if license_block else None
    resource_type_block = metadata.get("resource_type") or {}
    if isinstance(resource_type_block, dict):
        resource_type = (
            resource_type_block.get("type")
            or resource_type_block.get("title")
            or None
        )
    else:
        resource_type = str(resource_type_block) if resource_type_block else None
    concept_recid = item.get("conceptrecid")
    from src.index.zenodo_records.iri import doi_iri, record_iri  # noqa: PLC0415

    bare_id = str(item.get("id") or item.get("conceptrecid") or "")
    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}

    def _doi_url(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return doi_iri(value)

    def _int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return {
        "zenodo_id": record_iri(bare_id) if bare_id else "",
        "concept_recid": str(concept_recid) if concept_recid is not None else None,
        "doi": _doi_url(item.get("doi") or metadata.get("doi")),
        "concept_doi": _doi_url(item.get("conceptdoi")),
        "title": metadata.get("title"),
        "description": _strip_html(metadata.get("description")),
        "publication_date": _parse_publication_date(metadata.get("publication_date")),
        "resource_type": resource_type,
        "access_right": metadata.get("access_right"),
        "license_id": license_id,
        "version": metadata.get("version"),
        "revision": _int(item.get("revision")),
        # Top-level lifecycle timestamps; the API returns ISO-8601 strings
        # which DuckDB parses on insert via the column's TIMESTAMP type.
        "created_at": item.get("created"),
        "updated_at": item.get("updated"),
        # Reach metrics. Concept-level totals + this-version-only breakdown.
        "views": _int(stats.get("views")),
        "unique_views": _int(stats.get("unique_views")),
        "downloads": _int(stats.get("downloads")),
        "unique_downloads": _int(stats.get("unique_downloads")),
        "version_views": _int(stats.get("version_views")),
        "version_unique_views": _int(stats.get("version_unique_views")),
        "version_downloads": _int(stats.get("version_downloads")),
        "version_unique_downloads": _int(stats.get("version_unique_downloads")),
        "keywords": metadata.get("keywords") or [],
    }


def _project_creators(item: dict[str, Any]) -> list[tuple[dict[str, Any], int]]:
    metadata = item.get("metadata") or {}
    creators_raw = metadata.get("creators") or []
    out: list[tuple[dict[str, Any], int]] = []
    for position, raw in enumerate(creators_raw):
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        orcid = raw.get("orcid")
        creator_key = _slugify_creator_key(name, orcid)
        if not creator_key:
            continue
        out.append(
            (
                {
                    "creator_key": creator_key,
                    "display_name": name,
                    "orcid": (
                        f"https://orcid.org/{orcid}"
                        if orcid and not orcid.startswith("http")
                        else orcid
                    ),
                    "affiliation": raw.get("affiliation"),
                },
                position,
            ),
        )
    return out


def _project_communities(item: dict[str, Any]) -> list[str]:
    """Return canonical community IRIs, one per linked community.

    Slugs that look like grant numbers (`101060684`) or ISSNs (`1807-1260`)
    are kept, NOT dropped: those ARE real Zenodo communities (e.g. the
    BIORECER project community has slug `101060684`). Zenodo's direct
    `GET /api/communities/<slug>` returns an empty search envelope for numeric
    slugs, which made them look dead — but `fetch_by_slug` recovers them via a
    `?q=<slug>` search fallback. So the canonical community URL is correct here
    and resolution is the fetcher's job, not a reason to discard the link.
    """
    from src.index.zenodo_records.iri import community_iri  # noqa: PLC0415

    metadata = item.get("metadata") or {}
    blocks = metadata.get("communities") or []
    out: list[str] = []
    for b in blocks:
        slug = b.get("id") if isinstance(b, dict) else b
        if isinstance(slug, str) and slug.strip():
            out.append(community_iri(slug))
    return out


def _project_files(record_id: str, item: dict[str, Any]) -> list[dict[str, Any]]:
    files_raw = item.get("files") or []
    out: list[dict[str, Any]] = []
    for f in files_raw:
        if not isinstance(f, dict):
            continue
        key = f.get("key") or f.get("filename")
        if not key:
            continue
        links = f.get("links") or {}
        download_url = links.get("self") or links.get("download")
        out.append(
            {
                "record_id": record_id,
                "file_key": key,
                "file_id": f.get("id"),
                "size_bytes": f.get("size") or f.get("filesize"),
                "checksum": f.get("checksum"),
                "download_url": download_url,
            },
        )
    return out


def persist_record(
    store: ZenodoRecordsStore,
    item: dict[str, Any],
    *,
    crawling_community: str | None = None,
) -> str | None:
    row = _project_record(item)
    if not row["zenodo_id"]:
        return None
    record_id = row["zenodo_id"]

    # Mirror community membership onto the record itself so consumers
    # can filter `WHERE primary_community_id = 'cernopenlab'` or
    # `list_contains(community_ids, 'cernopenlab')` without joining
    # `record_communities`. `crawling_community` is the slug we were
    # iterating through when we found this record — useful as a
    # "primary" colour even when the record belongs to several.
    communities = _project_communities(item)
    row["community_ids"] = list(communities)
    from src.index.zenodo_records.iri import community_iri  # noqa: PLC0415

    if crawling_community and not row.get("primary_community_id"):
        # Callers pass bare slugs ("epfl") for the community they were
        # iterating; promote to the canonical IRI to match the new PK form.
        row["primary_community_id"] = community_iri(crawling_community)
    elif communities and not row.get("primary_community_id"):
        # `communities` already carries IRIs (see `_project_communities`).
        row["primary_community_id"] = communities[0]
    store.upsert_record(row, raw=item)

    creators = _project_creators(item)
    creator_positions: list[tuple[str, int]] = []
    for creator_row, position in creators:
        store.upsert_creator(creator_row, raw=creator_row)
        creator_positions.append((creator_row["creator_key"], position))
    if creator_positions:
        store.upsert_record_creators(record_id, creator_positions)

    if communities:
        # Keep the `communities` master table free of orphan references:
        # ensure a row exists for every community the record links to.
        # `ensure_community` is insert-if-absent, so it never overwrites
        # richer metadata written by the scope bootstrap pass.
        for cid in communities:
            store.ensure_community(cid)
        store.upsert_record_communities(record_id, communities)

    for file_row in _project_files(record_id, item):
        store.upsert_file(file_row)
    return record_id


def _state_path(config: ZenodoIndexConfig, scope_name: str) -> Path:
    return config.paths.state_dir / f"ingest_{scope_name}.json"


def _load_state(config: ZenodoIndexConfig, scope_name: str) -> dict[str, Any]:
    path = _state_path(config, scope_name)
    if not path.exists():
        return {"completed_communities": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        LOGGER.warning("could not parse %s; restarting state", path)
        return {"completed_communities": []}


def _save_state(config: ZenodoIndexConfig, scope_name: str, state: dict[str, Any]) -> None:
    _state_path(config, scope_name).write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


async def _ingest_async(
    *,
    config: ZenodoIndexConfig,
    store: ZenodoRecordsStore,
    scope: Scope,
    limit: int | None,
    refresh: bool,
) -> dict[str, int]:
    state = _load_state(config, scope.name) if not refresh else {"completed_communities": []}
    completed = set(state.get("completed_communities") or [])
    client = ZenodoClient(config)
    summary: dict[str, int] = {}
    seen_ids: set[str] = set()
    total_communities = len(scope.communities)

    LOGGER.info(
        "ingest start: scope=%s communities=%d already_completed=%d refresh=%s limit=%s",
        scope.name, total_communities, len(completed), refresh, limit,
    )

    # Eagerly upsert each community as a row so retrievers can join titles.
    # Emit a heartbeat every 25 communities so the operator can see we're
    # alive during the slow ~2.4s/slug Zenodo rate-limited fetch loop.
    LOGGER.info("[%s] community-metadata bootstrap starting…", scope.name)
    bootstrap_started_at = _time.time()
    for index, slug in enumerate(scope.communities, start=1):
        community = await client.fetch_community(slug)
        if community is None:
            LOGGER.warning("community %s not found on Zenodo; skipping", slug)
            continue
        from src.index.zenodo_records.iri import community_iri  # noqa: PLC0415

        store.upsert_community(
            {
                "community_id": community_iri(slug),
                "title": (community.get("metadata") or {}).get("title"),
            },
            raw=community,
        )
        if index % 25 == 0 or index == total_communities:
            elapsed = _time.time() - bootstrap_started_at
            LOGGER.info(
                "[%s] community-metadata bootstrap: %d/%d (%.0fs elapsed, ~%.1fs/comm)",
                scope.name, index, total_communities, elapsed, elapsed / index,
            )

    LOGGER.info("[%s] record ingestion starting…", scope.name)
    ingest_started_at = _time.time()
    last_heartbeat = ingest_started_at
    total_persisted = 0
    for community_index, slug in enumerate(scope.communities, start=1):
        if slug in completed and not refresh:
            LOGGER.info("scope=%s community=%s already completed; skipping", scope.name, slug)
            continue
        community_count = 0
        async for record in client.iter_records(slug, limit=limit):
            record_id = persist_record(store, record, crawling_community=slug)
            if record_id and record_id not in seen_ids:
                seen_ids.add(record_id)
                community_count += 1
                total_persisted += 1
            if community_count and community_count % 200 == 0:
                LOGGER.info(
                    "ingested %d records from community=%s (scope=%s)",
                    community_count, slug, scope.name,
                )
            # Time-based heartbeat (every ~60s) so an operator tailing
            # the log can confirm forward progress even when many
            # communities have 0 records.
            now = _time.time()
            if now - last_heartbeat >= 60.0:
                LOGGER.info(
                    "[%s] heartbeat: community=%d/%d total_records=%d elapsed=%.0fs",
                    scope.name, community_index, total_communities,
                    total_persisted, now - ingest_started_at,
                )
                last_heartbeat = now
        summary[slug] = community_count
        completed.add(slug)
        state["completed_communities"] = sorted(completed)
        _save_state(config, scope.name, state)
        LOGGER.info(
            "community=%s done: %d records (scope progress %d/%d, total_records=%d)",
            slug, community_count, community_index, total_communities, total_persisted,
        )
    LOGGER.info(
        "[%s] ingest done: %d records persisted across %d communities in %.0fs",
        scope.name, total_persisted, total_communities,
        _time.time() - ingest_started_at,
    )
    return summary


def ingest_records(
    *,
    config: ZenodoIndexConfig,
    store: ZenodoRecordsStore,
    scope: Scope,
    limit: int | None = None,
    refresh: bool = False,
) -> dict[str, int]:
    """Synchronous entrypoint used by the CLI."""
    return asyncio.run(
        _ingest_async(
            config=config,
            store=store,
            scope=scope,
            limit=limit,
            refresh=refresh,
        ),
    )


_DOI_TO_ID_RE = re.compile(r"10\.5281/zenodo\.(\d+)", re.IGNORECASE)


def _normalize_id_token(token: str) -> str | None:
    """Accept a numeric ID, a Zenodo DOI, or a Zenodo URL; return the numeric ID."""
    s = token.strip()
    if not s:
        return None
    m = _DOI_TO_ID_RE.search(s)
    if m:
        return m.group(1)
    m = re.search(r"zenodo\.org/(?:record/|records/|deposit/)?(\d+)", s)
    if m:
        return m.group(1)
    if s.isdigit():
        return s
    return None


def load_ids_file(path: Path) -> list[str]:
    """Read a newline-delimited file of Zenodo IDs / DOIs / URLs."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        rid = _normalize_id_token(line)
        if rid is None:
            LOGGER.warning("skip unparseable id token: %r", raw)
            continue
        if rid in seen:
            continue
        seen.add(rid)
        out.append(rid)
    return out


async def _ingest_by_ids_async(
    *,
    config: ZenodoIndexConfig,
    store: ZenodoRecordsStore,
    ids: list[str],
    refresh: bool,
) -> dict[str, Any]:
    existing = store.existing_record_ids(ids) if not refresh else set()
    pending = [rid for rid in ids if rid not in existing]
    client = ZenodoClient(config)

    persisted: list[str] = []
    missing: list[str] = []
    failed: list[dict[str, str]] = []
    fetched = 0

    async with httpx.AsyncClient() as http:
        for rid in pending:
            try:
                payload = await client.fetch_record(rid, client=http)
            except Exception as exc:  # noqa: BLE001
                failed.append({"id": rid, "error": str(exc)[:200]})
                continue
            if payload is None:
                missing.append(rid)
                continue
            fetched += 1
            persisted_id = persist_record(store, payload)
            if persisted_id:
                persisted.append(persisted_id)
            if fetched % 100 == 0:
                LOGGER.info("fetched %d/%d records", fetched, len(pending))

    return {
        "requested": len(ids),
        "skipped_existing": sorted(existing),
        "fetched": fetched,
        "persisted": persisted,
        "missing": missing,
        "failed": failed,
    }


def ingest_by_ids(
    *,
    config: ZenodoIndexConfig,
    store: ZenodoRecordsStore,
    ids: list[str],
    refresh: bool = False,
) -> dict[str, Any]:
    """Fetch a list of Zenodo records by ID and persist them.

    Skips IDs already present unless `refresh=True`.
    """
    return asyncio.run(
        _ingest_by_ids_async(config=config, store=store, ids=ids, refresh=refresh),
    )
