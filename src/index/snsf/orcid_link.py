"""Cross-source ORCID linkage: SNSF persons ↔ OpenAlex authors.

The SNSF `persons` table carries ORCIDs for ~50 % of EPFL responsible
applicants (and similar coverage for other CH institutions). The OpenAlex
sibling has `authors.orcid` for the same identifiers. This module joins
the two via DuckDB `ATTACH` so a query can return:

    (person, [snsf grants this person was responsible for],
             [openalex works this person authored])

Read-only on both stores, so it can run alongside ongoing ingest jobs as
long as no writer holds the OpenAlex DB lock.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import duckdb

from src.index.snsf.paths import duckdb_path as snsf_db_path

LOGGER = logging.getLogger(__name__)


# OpenAlex sibling DB lives next to ours under <INDEX_DATA_DIR>/openalex/.
def openalex_db_path() -> Path:
    return snsf_db_path().parent.parent.parent / "openalex" / "duckdb" / "openalex.duckdb"


_ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b")


def normalize_orcid(value: Optional[str]) -> Optional[str]:
    """Strip any URL prefix, return canonical 0000-0000-0000-000X form, or None."""
    if not value:
        return None
    m = _ORCID_RE.search(value)
    return m.group(0) if m else None


def open_joined(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """Open the SNSF DB with the OpenAlex DB ATTACHed read-only.

    Both DBs must exist. If the OpenAlex DB has a writer lock (someone is
    actively running `openalex.cli embed` or `build`), this will raise
    duckdb.IOException.
    """
    snsf = snsf_db_path()
    oa = openalex_db_path()
    if not snsf.exists():
        msg = (
            f"SNSF DuckDB not found: {snsf}. "
            f"Run `python -m src.index.snsf load-local` first."
        )
        raise FileNotFoundError(msg)
    if not oa.exists():
        msg = (
            f"OpenAlex DuckDB not found: {oa}. "
            f"Run `python -m src.index.openalex.cli ingest` first to build it."
        )
        raise FileNotFoundError(msg)
    conn = duckdb.connect(str(snsf), read_only=read_only)
    # ATTACH read-only so we don't conflict with any read-only handle on the
    # other side. If a writer holds OpenAlex this raises IOException.
    conn.execute(f"ATTACH '{oa}' AS oa (READ_ONLY)")
    return conn


def link_by_orcid(
    orcid: str,
    *,
    snsf_scope: Optional[str] = None,
    work_limit: int = 50,
    grant_limit: int = 50,
) -> Dict[str, Any]:
    """Look up one ORCID in both stores and return their grants + works."""
    norm = normalize_orcid(orcid)
    if norm is None:
        msg = f"Could not normalise to a 19-char ORCID: {orcid!r}"
        raise ValueError(msg)

    conn = open_joined(read_only=True)
    try:
        person = conn.execute(
            """
            SELECT person_number, first_name, last_name,
                   institute, research_institution, orcid
            FROM persons
            WHERE orcid = ?
            LIMIT 1
            """,
            [norm],
        ).fetchone()
        if person is None:
            return {"orcid": norm, "found_in": [], "snsf_grants": [], "openalex_works": []}
        pn, first, last, inst, ri, _ = person
        person_dict = {
            "person_number": int(pn) if pn is not None else None,
            "first_name": first, "last_name": last,
            "institute": inst, "research_institution": ri,
        }

        # SNSF grants — JOIN persons.responsible_applicant_grants (JSON)
        # back to grants. Optional scope filter.
        scope_join = (
            "JOIN scope_records s ON s.grant_number = g.grant_number "
            "AND s.scope_mode = ? "
        ) if snsf_scope else ""
        scope_params: List[Any] = [snsf_scope] if snsf_scope else []
        grants = conn.execute(
            f"""
            SELECT g.grant_number, g.title, g.research_institution,
                   g.main_discipline, g.start_date, g.amount_granted, g.state
            FROM persons p,
                 json_each(p.responsible_applicant_grants) AS j
            JOIN grants g ON g.grant_number = json_extract_string(j.value, '$')
            {scope_join}
            WHERE p.orcid = ?
            ORDER BY g.start_date DESC NULLS LAST
            LIMIT ?
            """,
            [*scope_params, norm, grant_limit],
        ).fetchall()
        gcols = ("grant_number", "title", "research_institution",
                 "main_discipline", "start_date", "amount_granted", "state")
        snsf_grants = [dict(zip(gcols, row, strict=False)) for row in grants]

        # OpenAlex authors — match by ORCID (URL form acceptable).
        oa_author = conn.execute(
            """
            SELECT openalex_id, display_name, orcid
            FROM oa.authors
            WHERE orcid = ? OR orcid = ?
            LIMIT 1
            """,
            [norm, f"https://orcid.org/{norm}"],
        ).fetchone()

        works: List[Dict[str, Any]] = []
        if oa_author is not None:
            oa_id = oa_author[0]
            # Join authors→work_authors→works for the openalex side.
            work_rows = conn.execute(
                """
                SELECT w.openalex_id, w.title, w.doi, w.publication_year,
                       w.primary_topic_id
                FROM oa.work_authors wa
                JOIN oa.works w ON w.openalex_id = wa.work_id
                WHERE wa.author_id = ?
                ORDER BY w.publication_year DESC NULLS LAST
                LIMIT ?
                """,
                [oa_id, work_limit],
            ).fetchall()
            wcols = ("openalex_id", "title", "doi", "publication_year", "primary_topic_id")
            works = [dict(zip(wcols, row, strict=False)) for row in work_rows]

        return {
            "orcid": norm,
            "person": person_dict,
            "openalex_author": (
                {"openalex_id": oa_author[0], "display_name": oa_author[1]}
                if oa_author is not None else None
            ),
            "snsf_scope": snsf_scope,
            "snsf_grants": snsf_grants,
            "openalex_works": works,
        }
    finally:
        conn.close()


def coverage_report(snsf_scope: Optional[str] = None) -> Dict[str, Any]:
    """How well does ORCID link the two stores for the given SNSF scope?"""
    conn = open_joined(read_only=True)
    try:
        scope_join = (
            "JOIN scope_records s ON s.grant_number = json_extract_string(j.value, '$') "
            "AND s.scope_mode = ?"
        ) if snsf_scope else ""
        params: List[Any] = [snsf_scope] if snsf_scope else []

        # SNSF persons with ORCID who are responsible applicants on grants
        # in the chosen scope (no scope = all).
        snsf_orcids = conn.execute(
            f"""
            SELECT DISTINCT p.orcid
            FROM persons p,
                 json_each(p.responsible_applicant_grants) AS j
            {scope_join}
            WHERE p.orcid IS NOT NULL AND length(p.orcid) > 0
            """,
            params,
        ).fetchall()
        snsf_orcid_set = {row[0] for row in snsf_orcids}

        # OpenAlex authors with ORCID (any).
        oa_orcids_rows = conn.execute(
            "SELECT orcid FROM oa.authors WHERE orcid IS NOT NULL AND length(orcid) > 0",
        ).fetchall()
        oa_orcid_set = {normalize_orcid(o) for (o,) in oa_orcids_rows}
        oa_orcid_set.discard(None)

        intersection = snsf_orcid_set & oa_orcid_set
        return {
            "snsf_scope": snsf_scope or "(all)",
            "snsf_responsible_applicants_with_orcid": len(snsf_orcid_set),
            "openalex_authors_with_orcid": len(oa_orcid_set),
            "linked_by_orcid": len(intersection),
            "link_ratio_snsf": (
                len(intersection) / len(snsf_orcid_set) if snsf_orcid_set else 0.0
            ),
        }
    finally:
        conn.close()


__all__ = [
    "coverage_report",
    "link_by_orcid",
    "normalize_orcid",
    "open_joined",
    "openalex_db_path",
]
