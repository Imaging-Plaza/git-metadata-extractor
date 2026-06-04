"""Extract relations stage: pull Person/Org authority UUIDs from Article JSON.

DSpace's `/relationships` endpoint is empty for Infoscience publications, but
the linked entity UUIDs are embedded as `authority` fields on
`dc.contributor.author` and several `cris.virtual.*` / `oairecerif.*` keys.

We parse `raw/items/{uuid}.json` for every Article in `matches.jsonl`, dedupe
the authority sets, and write `relations.jsonl` plus flat
`persons.txt` / `organizations.txt` for the next stage.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable, List, Optional, Set

from src.v2.canonicalization.infoscience import (
    infoscience_article_iri,
    infoscience_org_iri,
    infoscience_person_iri,
)

from .config import InfoscienceIndexConfig
from .extract_matches import load_matches
from .models import RelationRecord


def _canon_relation(rec: RelationRecord) -> RelationRecord:
    """v3.0.0: promote a RelationRecord's bare DSpace UUIDs to canonical
    Infoscience URLs so the reverse-relation maps key on the same ids as
    the Qdrant person/org records. Idempotent."""
    return RelationRecord(
        article_uuid=infoscience_article_iri(rec.article_uuid) or rec.article_uuid,
        person_uuids=[infoscience_person_iri(p) or p for p in rec.person_uuids],
        org_uuids=[infoscience_org_iri(o) or o for o in rec.org_uuids],
    )
from .paths import (
    organizations_set_path,
    persons_set_path,
    raw_items_dir,
    relations_path,
)

logger = logging.getLogger(__name__)

_PERSON_FIELDS = (
    "dc.contributor.author",
    "cris.virtualsource.author-scopus",
    "cris.virtualsource.author-orcid",
)

_ORG_FIELDS = (
    "cris.virtual.department",
    "cris.virtual.parent-organization",
    "oairecerif.author.affiliation",
    "cris.virtual.unitManager",  # actually a person; filtered below
    "dc.relation.journal",        # journal is a separate entity; collected
)

# Keep journal/unitManager separate so they don't pollute the org set;
# we collect orgs only from the explicit org-typed fields.
_ORG_ONLY_FIELDS = (
    "cris.virtual.department",
    "cris.virtual.parent-organization",
    "oairecerif.author.affiliation",
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _looks_like_uuid(s: Optional[str]) -> bool:
    return bool(s) and bool(_UUID_RE.match(s))


def _authorities(metadata: dict, fields: Iterable[str]) -> List[str]:
    out: List[str] = []
    for field in fields:
        for entry in metadata.get(field, []) or []:
            authority = entry.get("authority") if isinstance(entry, dict) else None
            if _looks_like_uuid(authority):
                out.append(authority)
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


def extract_relations(_cfg: InfoscienceIndexConfig) -> dict:
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
            person_uuids = _dedupe_preserve(_authorities(md, _PERSON_FIELDS))
            org_uuids = _dedupe_preserve(_authorities(md, _ORG_ONLY_FIELDS))
            if not person_uuids and not org_uuids:
                continue
            record = _canon_relation(RelationRecord(
                article_uuid=match.uuid,
                person_uuids=person_uuids,
                org_uuids=org_uuids,
            ))
            out.write(record.model_dump_json() + "\n")
            written += 1
            # The .txt sets are the fetch-by-UUID worklist for
            # `fetch_related` — keep them as bare DSpace UUIDs. Only the
            # relations.jsonl (above) carries the canonical URL ids.
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
            # Canonicalize on load so legacy relations.jsonl files (bare
            # UUIDs) still key the reverse maps by the URL ids.
            out.append(_canon_relation(RelationRecord(**json.loads(line))))
    return out


def load_set(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}


def run(cfg: InfoscienceIndexConfig) -> dict:
    return extract_relations(cfg)
