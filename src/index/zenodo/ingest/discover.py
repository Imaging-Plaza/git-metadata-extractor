"""Zenodo record discovery from external sources.

Today only the `infoscience` source is implemented: it scans the local
Infoscience full-text dump (`data/index/infoscience/text/*.txt`) for any
mention of `zenodo.org/...` URLs or `10.5281/zenodo.<id>` DOIs, then diffs
the extracted IDs against `records.zenodo_id` already in the Zenodo DuckDB.

The output `DiscoveryResult` is consumable by `ingest_by_ids` in
`src.index.zenodo.ingest.records` to actually fetch + persist the new
records via the Zenodo REST API.

The Infoscience server-side `fulltext:"zenodo.org/"` query (used by the
infoscience link-dump pipeline) misses ~80% of references because that
index is narrower than what we extracted from PDFs locally — hence the
need for a local-text scan.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.index.zenodo.storage.duckdb_store import ZenodoStore

LOGGER = logging.getLogger(__name__)

_RECORD_ID_RE = re.compile(r"zenodo\.org/(?:record/|records/|deposit/)?(\d{5,9})")
_DOI_RE = re.compile(r"10\.5281/zenodo\.(\d{5,9})", re.IGNORECASE)
_COMMUNITY_RE = re.compile(r"zenodo\.org/communities?/([a-zA-Z0-9_\-]{2,})")
# PDF text often line-wraps URLs and split numeric IDs; collapse those.
_LINEBREAK_AFTER_HOST_RE = re.compile(r"(zenodo\.org)[\s\-]*\n[\s\-]*")
_LINEBREAK_INSIDE_DIGITS_RE = re.compile(r"(\d)[\s\-]*\n[\s\-]*(\d)")


@dataclass
class DiscoveryResult:
    files_scanned: int = 0
    io_errors: int = 0
    files_with_zenodo: int = 0
    distinct_ids: list[str] = field(default_factory=list)
    new_ids: list[str] = field(default_factory=list)
    overlap_ids: list[str] = field(default_factory=list)
    communities_in_urls: dict[str, int] = field(default_factory=dict)
    file_to_rec: dict[str, list[str]] = field(default_factory=dict)


def _default_infoscience_text_dir() -> Path:
    from src.index.infoscience.paths import text_dir

    return text_dir()


def _read_text_resilient(path: str) -> str | None:
    """Read a text file once, with a single retry — overlay FS can be flaky."""
    for _ in range(2):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError:
            continue
    return None


def discover_from_infoscience(
    *,
    store: ZenodoStore,
    text_dir: Path | None = None,
) -> DiscoveryResult:
    """Scan Infoscience full-text and return Zenodo IDs to consider ingesting.

    `new_ids` is the diff against `records.zenodo_id` already in DuckDB.
    The result also reports which Infoscience UUIDs cited which Zenodo
    IDs (`file_to_rec`) and which Zenodo community slugs appeared in the
    extracted URL paths — useful for surfacing communities not yet in
    `config/index/zenodo.yaml`.
    """
    target_dir = text_dir or _default_infoscience_text_dir()
    record_ids: Counter[str] = Counter()
    dois: Counter[str] = Counter()
    communities: Counter[str] = Counter()
    file_to_rec: dict[str, list[str]] = {}
    files_scanned = io_errors = files_with_zenodo = 0

    for entry in os.scandir(target_dir):
        files_scanned += 1
        txt = _read_text_resilient(entry.path)
        if txt is None:
            io_errors += 1
            continue
        if "zenodo.org" not in txt and "10.5281/zenodo" not in txt:
            continue
        files_with_zenodo += 1
        cleaned = _LINEBREAK_AFTER_HOST_RE.sub(r"\1", txt)
        cleaned = _LINEBREAK_INSIDE_DIGITS_RE.sub(r"\1\2", cleaned)
        rid = _RECORD_ID_RE.findall(cleaned)
        did = _DOI_RE.findall(cleaned)
        com = _COMMUNITY_RE.findall(cleaned)
        record_ids.update(rid)
        dois.update(did)
        communities.update(com)
        if rid or did:
            uuid_key = entry.name.removesuffix(".txt")
            file_to_rec[uuid_key] = sorted(set(rid) | set(did))

    distinct = sorted(set(record_ids) | set(dois), key=int)
    have = store.existing_record_ids(distinct)
    new = [rid for rid in distinct if rid not in have]
    overlap = [rid for rid in distinct if rid in have]

    LOGGER.info(
        "infoscience discovery: scanned=%d io_errors=%d with_zenodo=%d "
        "distinct=%d overlap=%d new=%d",
        files_scanned,
        io_errors,
        files_with_zenodo,
        len(distinct),
        len(overlap),
        len(new),
    )

    return DiscoveryResult(
        files_scanned=files_scanned,
        io_errors=io_errors,
        files_with_zenodo=files_with_zenodo,
        distinct_ids=distinct,
        new_ids=new,
        overlap_ids=overlap,
        communities_in_urls=dict(communities),
        file_to_rec=file_to_rec,
    )
