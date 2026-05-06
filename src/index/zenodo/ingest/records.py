"""Project Zenodo record JSON → DuckDB rows and persist."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from bs4 import BeautifulSoup

from src.index.zenodo.ingest.scope import Scope
from src.index.zenodo.ingest.zenodo_client import ZenodoClient

if TYPE_CHECKING:
    from src.index.zenodo.config import ZenodoIndexConfig
    from src.index.zenodo.storage.duckdb_store import ZenodoStore

LOGGER = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _strip_html(raw: str | None) -> str | None:
    if not raw:
        return None
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
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
    return {
        "zenodo_id": str(item.get("id") or item.get("conceptrecid") or ""),
        "concept_recid": str(concept_recid) if concept_recid is not None else None,
        "doi": item.get("doi") or metadata.get("doi"),
        "title": metadata.get("title"),
        "description": _strip_html(metadata.get("description")),
        "publication_date": _parse_publication_date(metadata.get("publication_date")),
        "resource_type": resource_type,
        "access_right": metadata.get("access_right"),
        "license_id": license_id,
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
    metadata = item.get("metadata") or {}
    blocks = metadata.get("communities") or []
    out: list[str] = []
    for b in blocks:
        if isinstance(b, dict) and b.get("id"):
            out.append(str(b["id"]))
        elif isinstance(b, str):
            out.append(b)
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


def persist_record(store: ZenodoStore, item: dict[str, Any]) -> str | None:
    row = _project_record(item)
    if not row["zenodo_id"]:
        return None
    record_id = row["zenodo_id"]
    store.upsert_record(row, raw=item)

    creators = _project_creators(item)
    creator_positions: list[tuple[str, int]] = []
    for creator_row, position in creators:
        store.upsert_creator(creator_row, raw=creator_row)
        creator_positions.append((creator_row["creator_key"], position))
    if creator_positions:
        store.upsert_record_creators(record_id, creator_positions)

    communities = _project_communities(item)
    if communities:
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
    store: ZenodoStore,
    scope: Scope,
    limit: int | None,
    refresh: bool,
) -> dict[str, int]:
    state = _load_state(config, scope.name) if not refresh else {"completed_communities": []}
    completed = set(state.get("completed_communities") or [])
    client = ZenodoClient(config)
    summary: dict[str, int] = {}
    seen_ids: set[str] = set()

    # Eagerly upsert each community as a row so retrievers can join titles.
    for slug in scope.communities:
        community = await client.fetch_community(slug)
        if community is None:
            LOGGER.warning("community %s not found on Zenodo; skipping", slug)
            continue
        store.upsert_community(
            {"community_id": slug, "title": (community.get("metadata") or {}).get("title")},
            raw=community,
        )

    for slug in scope.communities:
        if slug in completed and not refresh:
            LOGGER.info("scope=%s community=%s already completed; skipping", scope.name, slug)
            continue
        community_count = 0
        async for record in client.iter_records(slug, limit=limit):
            record_id = persist_record(store, record)
            if record_id and record_id not in seen_ids:
                seen_ids.add(record_id)
                community_count += 1
            if community_count and community_count % 200 == 0:
                LOGGER.info(
                    "ingested %d records from community=%s (scope=%s)",
                    community_count,
                    slug,
                    scope.name,
                )
        summary[slug] = community_count
        completed.add(slug)
        state["completed_communities"] = sorted(completed)
        _save_state(config, scope.name, state)
        LOGGER.info(
            "community=%s done: %d records persisted",
            slug,
            community_count,
        )
    return summary


def ingest_records(
    *,
    config: ZenodoIndexConfig,
    store: ZenodoStore,
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
    store: ZenodoStore,
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
    store: ZenodoStore,
    ids: list[str],
    refresh: bool = False,
) -> dict[str, Any]:
    """Fetch a list of Zenodo records by ID and persist them.

    Skips IDs already present unless `refresh=True`.
    """
    return asyncio.run(
        _ingest_by_ids_async(config=config, store=store, ids=ids, refresh=refresh),
    )
