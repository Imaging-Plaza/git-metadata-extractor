"""Lazy in-memory inverted index over the FULL ROR dump.

Used by `lookup_dump()` for exact-ID, name/alias substring, and country-code
queries against the entire worldwide registry — no embeddings, no RCP calls.
The dump JSON is streamed once on first call and cached as a process-local
singleton; expect ~250–350 MB RSS for the v2.2 release (~122k records).

Token matching is case-folded and accent-stripped (so `École` ↔ `Ecole`).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .document import display_name
from .models import DumpMatch

logger = logging.getLogger(__name__)


def _fold(text: str) -> str:
    """Lower-case + strip combining marks (NFKD then drop Mn category)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(_fold(text))


def _bare_id(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1]


class DumpIndex:
    """In-memory ROR dump index. Build once via `from_json_path`, query many."""

    def __init__(self) -> None:
        self._records: Dict[str, Dict[str, Any]] = {}
        self._token_index: Dict[str, Set[str]] = defaultdict(set)
        self._country_index: Dict[str, Set[str]] = defaultdict(set)

    @property
    def size(self) -> int:
        return len(self._records)

    @classmethod
    def from_records(cls, records: Iterable[Dict[str, Any]]) -> "DumpIndex":
        idx = cls()
        for record in records:
            idx._add(record)
        return idx

    @classmethod
    def from_json_path(cls, path: Path) -> "DumpIndex":
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, list):
            msg = (
                f"Expected ROR dump JSON to be a list at the top level "
                f"({path.name})"
            )
            raise ValueError(msg)
        idx = cls.from_records(data)
        logger.info("Loaded ROR dump index: %d records from %s", idx.size, path)
        return idx

    def _add(self, record: Dict[str, Any]) -> None:
        rid = record.get("id")
        if not isinstance(rid, str) or not rid:
            return
        normalized = rid.rstrip("/")
        bare = _bare_id(normalized)
        self._records[normalized] = record
        self._records[bare] = record

        seen_tokens: Set[str] = set()
        for entry in record.get("names") or []:
            value = entry.get("value") if isinstance(entry, dict) else None
            if isinstance(value, str) and value:
                for tok in _tokenize(value):
                    seen_tokens.add(tok)
        for tok in seen_tokens:
            self._token_index[tok].add(normalized)

        for loc in record.get("locations") or []:
            details = loc.get("geonames_details") if isinstance(loc, dict) else None
            cc = details.get("country_code") if isinstance(details, dict) else None
            if isinstance(cc, str) and cc:
                self._country_index[cc.upper()].add(normalized)

    def by_id(self, ror_id: str) -> Optional[Dict[str, Any]]:
        ror_id = ror_id.strip().rstrip("/")
        return self._records.get(ror_id) or self._records.get(_bare_id(ror_id))

    def by_country(self, country_code: str) -> List[Dict[str, Any]]:
        ids = self._country_index.get(country_code.upper(), set())
        return [self._records[i] for i in ids if i in self._records]

    def search(
        self,
        text: Optional[str] = None,
        *,
        ror_id: Optional[str] = None,
        country: Optional[str] = None,
        limit: int = 20,
    ) -> List[DumpMatch]:
        """Lookup with any combination of (text, ror_id, country)."""
        if ror_id:
            record = self.by_id(ror_id)
            if record is None:
                return []
            return [DumpMatch(
                ror_id=str(record.get("id", "")),
                name=display_name(record),
                record=record,
                matched_tokens=[],
            )]

        candidate_ids: Optional[Set[str]] = None
        matched_tokens: List[str] = []

        if text:
            tokens = _tokenize(text)
            if not tokens:
                return []
            scored: Dict[str, int] = defaultdict(int)
            for tok in tokens:
                hits = self._token_index.get(tok)
                if not hits:
                    continue
                matched_tokens.append(tok)
                for rid in hits:
                    scored[rid] += 1
            if not scored:
                return []
            ranked = sorted(
                scored.items(), key=lambda kv: (-kv[1], kv[0]),
            )
            candidate_ids = {rid for rid, _ in ranked}
            ordered = [rid for rid, _ in ranked]
        else:
            ordered = list(self._token_index.get("", set()))  # empty path
            candidate_ids = set(ordered)

        if country:
            country_ids = self._country_index.get(country.upper(), set())
            candidate_ids = (candidate_ids or country_ids) & country_ids
            if not candidate_ids:
                return []
            ordered = [rid for rid in ordered if rid in candidate_ids] if text else list(country_ids)

        results: List[DumpMatch] = []
        for rid in ordered[:limit]:
            record = self._records.get(rid)
            if record is None:
                continue
            results.append(DumpMatch(
                ror_id=str(record.get("id", "")),
                name=display_name(record),
                record=record,
                matched_tokens=matched_tokens,
            ))
        return results


_LOCK = threading.Lock()
_CACHED_INDEX: Optional[Tuple[Path, DumpIndex]] = None


def get_dump_index(json_path: Path) -> DumpIndex:
    """Process-local singleton; rebuilt only if the path changes."""
    global _CACHED_INDEX  # noqa: PLW0603
    json_path = Path(json_path)
    with _LOCK:
        if _CACHED_INDEX is not None and _CACHED_INDEX[0] == json_path:
            return _CACHED_INDEX[1]
        idx = DumpIndex.from_json_path(json_path)
        _CACHED_INDEX = (json_path, idx)
        return idx


def reset_cached_dump_index() -> None:
    """Test hook — drop the singleton."""
    global _CACHED_INDEX  # noqa: PLW0603
    with _LOCK:
        _CACHED_INDEX = None


__all__: List[str] = [
    "DumpIndex",
    "get_dump_index",
    "reset_cached_dump_index",
]
