"""build_facets: populate the three derived facet tables from SNSF source data.

The tables (grant_persons, grant_output_counts, grant_countries) are defined in
storage/facets.sql and are also appended to storage/schema.sql so they exist
whenever SnsfStore.bootstrap() runs.

build_facets() is idempotent: it DELETEs all rows from the three tables before
rebuilding them from the source tables (grants, persons, output_*).  Re-running
after any reload is safe and produces correct counts.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.index.snsf.storage.duckdb_store import SnsfStore

_FACETS_SQL_PATH = Path(__file__).parent / "storage" / "facets.sql"

# (role name, column name in the persons table)
_ROLE_COL_PAIRS: list[tuple[str, str]] = [
    ("responsible_applicant", "responsible_applicant_grants"),
    ("co_applicant", "co_applicant_grants"),
    ("project_partner", "project_partner_grants"),
    ("practice_partner", "practice_partner_grants"),
    ("employee", "employee_grants"),
    ("contact_person", "contact_person_grants"),
    ("applicant_abroad", "applicant_abroad_grants"),
]


def build_facets(store: SnsfStore) -> dict[str, int]:
    """(Re)build the three derived facet tables from the SNSF source tables.

    Steps:
    1. Ensure facet tables exist (execute facets.sql — idempotent DDL).
    2. Inside a transaction, DELETE all rows from the three facet tables.
    3. Re-populate from the source tables.
    4. Return row counts: {"grant_persons": n, "grant_output_counts": n,
       "grant_countries": n}.
    """
    conn = store.connect()

    # Ensure tables exist (idempotent DDL)
    facets_ddl = _FACETS_SQL_PATH.read_text(encoding="utf-8")
    conn.execute(facets_ddl)

    with store.transaction():
        # --- Clear ---
        conn.execute("DELETE FROM grant_persons")
        conn.execute("DELETE FROM grant_output_counts")
        conn.execute("DELETE FROM grant_countries")

        # --- grant_persons: flatten each role's JSON grant-URL array ---
        for role, col in _ROLE_COL_PAIRS:
            conn.execute(
                f"""
                INSERT INTO grant_persons (grant_number, person_number, role)
                SELECT DISTINCT json_extract_string(j.value, '$'),
                                p.person_number,
                                '{role}'
                FROM   persons p,
                       json_each(p.{col}) AS j
                WHERE  p.{col} IS NOT NULL
                  AND  json_extract_string(j.value, '$') IS NOT NULL
                ON CONFLICT DO NOTHING
                """,  # noqa: S608 — role and col are fixed strings from _ROLE_COL_PAIRS
            )

        # --- grant_output_counts: per-grant rollup across all 7 output tables ---
        conn.execute(
            """
            INSERT INTO grant_output_counts (
                grant_number,
                n_publications,
                n_datasets,
                n_collaborations,
                n_academic_events,
                n_knowledge_transfers,
                n_public_communications,
                n_use_inspired
            )
            SELECT
                g.grant_number,
                COALESCE(pub.c, 0),
                COALESCE(ds.c,  0),
                COALESCE(col.c, 0),
                COALESCE(ae.c,  0),
                COALESCE(kt.c,  0),
                COALESCE(pc.c,  0),
                COALESCE(ui.c,  0)
            FROM grants g
            LEFT JOIN (
                SELECT grant_number, count(*) c FROM output_publications
                GROUP BY grant_number
            ) pub ON pub.grant_number = g.grant_number
            LEFT JOIN (
                SELECT grant_number, count(*) c FROM output_datasets
                GROUP BY grant_number
            ) ds  ON ds.grant_number  = g.grant_number
            LEFT JOIN (
                SELECT grant_number, count(*) c FROM output_collaborations
                GROUP BY grant_number
            ) col ON col.grant_number = g.grant_number
            LEFT JOIN (
                SELECT grant_number, count(*) c FROM output_academic_events
                GROUP BY grant_number
            ) ae  ON ae.grant_number  = g.grant_number
            LEFT JOIN (
                SELECT grant_number, count(*) c FROM output_knowledge_transfers
                GROUP BY grant_number
            ) kt  ON kt.grant_number  = g.grant_number
            LEFT JOIN (
                SELECT grant_number, count(*) c FROM output_public_communications
                GROUP BY grant_number
            ) pc  ON pc.grant_number  = g.grant_number
            LEFT JOIN (
                SELECT grant_number, count(*) c FROM output_use_inspired
                GROUP BY grant_number
            ) ui  ON ui.grant_number  = g.grant_number
            """,
        )

        # --- grant_countries: distinct country per grant from collaborations ---
        conn.execute(
            """
            INSERT INTO grant_countries (grant_number, country)
            SELECT DISTINCT grant_number, country
            FROM   output_collaborations
            WHERE  country     IS NOT NULL
            AND    TRIM(country) <> ''
            AND    grant_number IS NOT NULL
            ON CONFLICT DO NOTHING
            """,
        )

    # --- Return counts ---
    def _count(table: str) -> int:
        result = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
        return int(result[0]) if result else 0

    return {
        "grant_persons": _count("grant_persons"),
        "grant_output_counts": _count("grant_output_counts"),
        "grant_countries": _count("grant_countries"),
    }


__all__ = ["build_facets"]
