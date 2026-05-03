"""Synthesize OrgUnit records from `person.department` text.

ETH Research Collection's DSpace 7 deployment does not expose OrgUnit
entities with stable UUIDs (the ``/relationships`` subresource on Person
items is empty, and the ``/leitzahl`` HAL endpoint returns no body). The
only organisational signal on a Person record is the free-text
``person.department`` field, which follows a consistent pattern:

    "<5-digit-leitzahl> - <lab name> [/ <head name>]"

Examples observed in pilot data:

    "03996 - Benini, Luca / Benini, Luca"
    "08686 - Gruppe Strassenverkehrstechnik"
    "03736 - Reiher, Markus / Reiher, Markus"

This module scans ``raw/persons/*.json`` for the unique set of
``person.department`` values, parses each into ``(leitzahl, lab_name,
head_name)``, and writes one synthetic Org JSON per leitzahl into
``raw/organizations/`` plus the leitzahl→uuid set into ``organizations.txt``
so the downstream ``ingest-duckdb`` and ``embed`` stages can process them
without further changes.

The synthetic UUID is ``uuid5(_LEITZAHL_NAMESPACE, leitzahl)`` so the
mapping is stable across rebuilds.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Optional

from .config import EthzResearchCollectionIndexConfig
from .paths import (
    organizations_set_path,
    raw_organizations_dir,
    raw_persons_dir,
)

logger = logging.getLogger(__name__)

# Stable namespace for OrgUnit synth UUIDs derived from ETH leitzahl codes.
# Distinct from src/index/ethz_research_collection/store.py's index namespace
# so we don't collide with any future real OrgUnit ingestion.
_LEITZAHL_NAMESPACE = uuid.UUID("c1e2d3f4-5a6b-7c8d-9e0f-112233445566")

# Strict ETH leitzahl: 5 digits, hyphen, rest of name. Some entries lack
# a "/ <head>" tail, so the head capture is optional.
_DEPT_RE = re.compile(
    r"""
    ^\s*
    (?P<leitzahl>\d{5})    # 5-digit Leitzahl
    \s*-\s*
    (?P<name>[^/]+?)       # lab name (everything up to optional ' / ')
    (?:\s*/\s*(?P<head>.+?))?
    \s*$
    """,
    re.VERBOSE,
)


def _synth_uuid(leitzahl: str) -> str:
    return str(uuid.uuid5(_LEITZAHL_NAMESPACE, f"ethz-leitzahl-{leitzahl}"))


def _parse_department(text: str) -> Optional[dict]:
    """Return ``{leitzahl, name, head}`` or ``None`` if the text is malformed."""
    if not isinstance(text, str):
        return None
    m = _DEPT_RE.match(text.strip())
    if not m:
        return None
    leitzahl = m.group("leitzahl")
    name = (m.group("name") or "").strip()
    head = (m.group("head") or "").strip() or None
    if not name:
        return None
    return {"leitzahl": leitzahl, "name": name, "head": head}


def _build_synthetic_item(parsed: dict) -> dict:
    """Build a DSpace-shaped Org JSON from a parsed department row."""
    leitzahl = parsed["leitzahl"]
    name = parsed["name"]
    head = parsed.get("head")
    description_parts = [f"ETH Zürich Leitzahl {leitzahl}"]
    if head and head != name:
        description_parts.append(f"Head: {head}")
    if head and head == name:
        # When the lab is named after a single PI, the head and lab name
        # collapse to one string; mention the role explicitly.
        description_parts.append(f"Professorship of {name}")
    description = "; ".join(description_parts)

    return {
        "uuid": _synth_uuid(leitzahl),
        "name": name,
        "type": "item",
        "_synthetic": True,
        "_synthetic_source": "ethz_leitzahl_from_person.department",
        "metadata": {
            "dc.title": [{"value": name, "language": None,
                          "authority": None, "confidence": -1, "place": 0}],
            "organization.identifier.acronym": [{
                "value": leitzahl, "language": None,
                "authority": None, "confidence": -1, "place": 0,
            }],
            "dc.description": [{"value": description, "language": None,
                                "authority": None, "confidence": -1, "place": 0}],
            "epfl.unitId": [{"value": leitzahl, "language": None,
                             "authority": None, "confidence": -1, "place": 0}],
            "dspace.entity.type": [{"value": "OrgUnit", "language": None,
                                    "authority": None, "confidence": -1, "place": 0}],
        },
    }


def synthesize_organizations(_cfg: EthzResearchCollectionIndexConfig) -> dict:
    """Mine ``person.department`` text → write synthetic Org JSONs."""
    persons_dir = raw_persons_dir()
    orgs_dir = raw_organizations_dir()
    orgs_dir.mkdir(parents=True, exist_ok=True)

    seen_dept_text: set[str] = set()
    written = 0
    skipped_existing = 0
    malformed: list[str] = []
    leitzahls: dict[str, str] = {}  # leitzahl → synth uuid

    for person_path in sorted(persons_dir.glob("*.json")):
        try:
            person = json.loads(person_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("synth_orgs: failed to read %s — %s", person_path.name, exc)
            continue
        md = person.get("metadata", {}) or {}
        for entry in md.get("person.department", []) or []:
            text = entry.get("value") if isinstance(entry, dict) else None
            if not text or text in seen_dept_text:
                continue
            seen_dept_text.add(text)
            parsed = _parse_department(text)
            if parsed is None:
                malformed.append(text)
                continue
            org = _build_synthetic_item(parsed)
            leitzahls[parsed["leitzahl"]] = org["uuid"]
            target = orgs_dir / f"{org['uuid']}.json"
            if target.exists():
                skipped_existing += 1
                continue
            target.write_text(
                json.dumps(org, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            written += 1

    organizations_set_path().write_text(
        "\n".join(sorted(leitzahls.values())),
        encoding="utf-8",
    )

    summary = {
        "distinct_department_texts": len(seen_dept_text),
        "distinct_leitzahls": len(leitzahls),
        "orgs_written": written,
        "orgs_skipped_existing": skipped_existing,
        "malformed_department_texts": malformed[:5] + (["…"] if len(malformed) > 5 else []),
        "raw_orgs_dir": str(orgs_dir),
    }
    logger.info("synth_orgs: %s", summary)
    return summary


def run(cfg: EthzResearchCollectionIndexConfig) -> dict:
    return synthesize_organizations(cfg)


__all__ = ["run", "synthesize_organizations"]
