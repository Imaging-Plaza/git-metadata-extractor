"""Extract relations stage: pull Person/Org UUIDs from Article JSON.

ETH Research Collection (DSpace 7) embeds linked-entity UUIDs in
``relation.isAuthorOfPublication[*].value`` — each entry's ``authority``
is a virtual slot ID (``virtual::N``) and ``value`` is the actual Person
UUID. Journals follow the same shape under
``relation.isJournalOfPublication``.

This differs from EPFL Infoscience, which embeds UUIDs in
``cris.virtualsource.*`` / ``oairecerif.*`` ``authority`` fields. The
sister extractor in ``src/index/infoscience`` reads the latter; this
file reads ETH-RC's ``relation.is*OfPublication.value`` shape.

We parse ``raw/items/{uuid}.json`` for every Article in ``matches.jsonl``,
dedupe the UUID sets, and write ``relations.jsonl`` plus flat
``persons.txt`` / ``organizations.txt`` for the next stage.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable, List, Optional, Set

from .config import EthzResearchCollectionIndexConfig
from .extract_matches import load_matches
from .models import RelationRecord
from .paths import (
    organizations_set_path,
    persons_set_path,
    raw_items_dir,
    relations_path,
)

logger = logging.getLogger(__name__)

# DSpace 7 ``relation.is*OfPublication`` fields carry the linked entity's
# UUID in ``value`` (and a virtual slot id in ``authority``). For ETH RC
# author UUIDs and journal UUIDs both follow this pattern.
_PERSON_RELATION_FIELDS = (
    "relation.isAuthorOfPublication",
)

# ETH RC publication metadata does not expose a direct OrgUnit relation
# (no equivalent of infoscience's ``cris.virtual.department``). Org-side
# affiliations are reachable via the Person record's employment data
# (fetched in the ``fetch-related`` stage). Keep this tuple empty so the
# article-level org set stays empty until the Person stage populates it
# transitively.
_ORG_RELATION_FIELDS: tuple[str, ...] = ()

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _looks_like_uuid(s: Optional[str]) -> bool:
    return bool(s) and bool(_UUID_RE.match(s))


def _relation_values(metadata: dict, fields: Iterable[str]) -> List[str]:
    """Read UUID strings from the ``value`` slot of each relation entry."""
    out: List[str] = []
    for field in fields:
        for entry in metadata.get(field, []) or []:
            value = entry.get("value") if isinstance(entry, dict) else None
            if _looks_like_uuid(value):
                out.append(value)
    return out


def _load_item(uuid: str) -> Optional[dict]:
    p = raw_items_dir() / f"{uuid}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _dedupe_preserve(seq: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for x in seq:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def extract_relations(_cfg: EthzResearchCollectionIndexConfig) -> dict:
    matches = load_matches()
    if not matches:
        logger.warning("No matches.jsonl entries. Run `extract-matches` first.")
        relations_path().write_text("", encoding="utf-8")
        return {"articles": 0, "persons": 0, "organizations": 0}

    persons: Set[str] = set()
    orgs: Set[str] = set()
    written = 0

    with relations_path().open("w", encoding="utf-8") as out:
        for match in matches:
            item = _load_item(match.uuid)
            if item is None:
                logger.debug("Missing raw item for matched uuid %s", match.uuid)
                continue
            md = item.get("metadata", {}) or {}
            person_uuids = _dedupe_preserve(
                _relation_values(md, _PERSON_RELATION_FIELDS),
            )
            org_uuids = _dedupe_preserve(
                _relation_values(md, _ORG_RELATION_FIELDS),
            )
            if not person_uuids and not org_uuids:
                continue
            record = RelationRecord(
                article_uuid=match.uuid,
                person_uuids=person_uuids,
                org_uuids=org_uuids,
            )
            out.write(record.model_dump_json() + "\n")
            written += 1
            persons.update(person_uuids)
            orgs.update(org_uuids)

    persons_set_path().write_text("\n".join(sorted(persons)), encoding="utf-8")
    organizations_set_path().write_text("\n".join(sorted(orgs)), encoding="utf-8")

    summary = {
        "articles": written,
        "persons": len(persons),
        "organizations": len(orgs),
        "relations_path": str(relations_path()),
    }
    logger.info("extract_relations: %s", summary)
    return summary


def load_relations() -> List[RelationRecord]:
    p = relations_path()
    if not p.exists():
        return []
    out: List[RelationRecord] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(RelationRecord(**json.loads(line)))
    return out


def load_set(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}


def run(cfg: EthzResearchCollectionIndexConfig) -> dict:
    return extract_relations(cfg)
