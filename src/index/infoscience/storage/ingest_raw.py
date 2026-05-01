"""Ingest from on-disk raw/* JSON tree → DuckDB.

Uses DuckDB's native `read_json_objects` to slurp every file in parallel
in C++ — orders of magnitude faster than the previous Python-level loop,
which choked on WSL2's 9P filesystem when walking ~16k small JSON files.

The discover / fetch-related stages and `scripts/dump_link_articles.py`
write their output to:

    data/index/infoscience/raw/items/{uuid}.json
    data/index/infoscience/raw/persons/{uuid}.json
    data/index/infoscience/raw/organizations/{uuid}.json

This module reads each glob, projects the DSpace metadata fields we
care about into typed columns via JSON path expressions, and upserts
into the DuckDB tables defined in `schema.sql`. Idempotent on UUID.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from src.index.infoscience.paths import (
    raw_items_dir,
    raw_organizations_dir,
    raw_persons_dir,
)

from .duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------

# Note: DSpace metadata field names contain dots, so the JSON path needs them
# quoted. Path templates are reused for both the article columns and the
# child unnest queries.
_ARTICLE_UPSERT_SQL = """
CREATE OR REPLACE TEMP TABLE _items AS
  SELECT json FROM read_json_objects(?);

INSERT INTO articles
  (article_uuid, title, abstract, doi, publication_year, publication_type,
   journal, language, infoscience_url, raw, ingested_at)
SELECT
  json_extract_string(json, '$.uuid') AS article_uuid,
  json_extract_string(json, '$.metadata."dc.title"[0].value') AS title,
  json_extract_string(json, '$.metadata."dc.description.abstract"[0].value') AS abstract,
  json_extract_string(json, '$.metadata."dc.identifier.doi"[0].value') AS doi,
  TRY_CAST(
    SUBSTR(json_extract_string(json, '$.metadata."dc.date.issued"[0].value'), 1, 4)
    AS INTEGER
  ) AS publication_year,
  json_extract_string(json, '$.metadata."dc.type"[0].value') AS publication_type,
  json_extract_string(json, '$.metadata."dc.relation.journal"[0].value') AS journal,
  json_extract_string(json, '$.metadata."dc.language.iso"[0].value') AS language,
  'https://infoscience.epfl.ch/entities/publication/' || json_extract_string(json, '$.uuid') AS infoscience_url,
  json AS raw,
  CURRENT_TIMESTAMP AS ingested_at
FROM _items
WHERE json_extract_string(json, '$.uuid') IS NOT NULL
ON CONFLICT (article_uuid) DO UPDATE SET
  title            = excluded.title,
  abstract         = excluded.abstract,
  doi              = excluded.doi,
  publication_year = excluded.publication_year,
  publication_type = excluded.publication_type,
  journal          = excluded.journal,
  language         = excluded.language,
  infoscience_url  = excluded.infoscience_url,
  raw              = excluded.raw,
  ingested_at      = excluded.ingested_at;
"""

_ARTICLE_PERSONS_SQL = """
INSERT INTO article_persons (article_uuid, person_uuid, position)
SELECT
  json_extract_string(i.json, '$.uuid') AS article_uuid,
  json_extract_string(t.author, '$.authority') AS person_uuid,
  TRY_CAST(json_extract_string(t.author, '$.place') AS INTEGER) AS position
FROM _items i,
     UNNEST(
       CAST(
         json_extract(i.json, '$.metadata."dc.contributor.author"') AS JSON[]
       )
     ) AS t(author)
WHERE json_extract_string(t.author, '$.authority') IS NOT NULL
ON CONFLICT (article_uuid, person_uuid) DO UPDATE SET
  position = COALESCE(
    LEAST(article_persons.position, excluded.position),
    excluded.position,
    article_persons.position
  );
"""

# Unnest each org-bearing metadata field separately and union the rows so
# the `field` column records which DSpace key the authority came from.
_ARTICLE_ORGS_SQL_TEMPLATE = """
INSERT INTO article_orgs (article_uuid, org_uuid, field)
SELECT article_uuid, org_uuid, field FROM (
  {unions}
) sub
WHERE org_uuid IS NOT NULL
ON CONFLICT DO NOTHING;
"""

_ORG_FIELDS = (
    "cris.virtual.department",
    "cris.virtual.parent-organization",
    "oairecerif.author.affiliation",
)


def _orgs_union_sql() -> str:
    parts = []
    for field in _ORG_FIELDS:
        parts.append(
            f"""
            SELECT
              json_extract_string(i.json, '$.uuid') AS article_uuid,
              json_extract_string(t.org, '$.authority') AS org_uuid,
              '{field}' AS field
            FROM _items i,
                 UNNEST(
                   CAST(
                     json_extract(i.json, '$.metadata."{field}"') AS JSON[]
                   )
                 ) AS t(org)
            """
        )
    return " UNION ALL ".join(parts)


# ---------------------------------------------------------------------------
# Persons
# ---------------------------------------------------------------------------

_PERSON_UPSERT_SQL = """
CREATE OR REPLACE TEMP TABLE _persons AS
  SELECT json FROM read_json_objects(?);

INSERT INTO persons
  (person_uuid, display_name, given_name, family_name, orcid, sciper_id,
   primary_affiliation, primary_affiliation_uuid, raw, ingested_at)
SELECT
  json_extract_string(json, '$.uuid') AS person_uuid,
  COALESCE(
    json_extract_string(json, '$.metadata."dc.title"[0].value'),
    TRIM(
      COALESCE(json_extract_string(json, '$.metadata."person.givenName"[0].value'), '')
      || ' ' ||
      COALESCE(json_extract_string(json, '$.metadata."person.familyName"[0].value'), '')
    )
  ) AS display_name,
  COALESCE(
    json_extract_string(json, '$.metadata."person.givenName"[0].value'),
    json_extract_string(json, '$.metadata."eperson.firstname"[0].value')
  ) AS given_name,
  COALESCE(
    json_extract_string(json, '$.metadata."person.familyName"[0].value'),
    json_extract_string(json, '$.metadata."eperson.lastname"[0].value')
  ) AS family_name,
  json_extract_string(json, '$.metadata."person.identifier.orcid"[0].value') AS orcid,
  COALESCE(
    json_extract_string(json, '$.metadata."epfl.sciperId"[0].value'),
    json_extract_string(json, '$.metadata."cris.virtual.sciperId"[0].value')
  ) AS sciper_id,
  json_extract_string(json, '$.metadata."person.affiliation.name"[0].value') AS primary_affiliation,
  json_extract_string(json, '$.metadata."person.affiliation.name"[0].authority') AS primary_affiliation_uuid,
  json AS raw,
  CURRENT_TIMESTAMP AS ingested_at
FROM _persons
WHERE json_extract_string(json, '$.uuid') IS NOT NULL
ON CONFLICT (person_uuid) DO UPDATE SET
  display_name             = excluded.display_name,
  given_name               = excluded.given_name,
  family_name              = excluded.family_name,
  orcid                    = excluded.orcid,
  sciper_id                = excluded.sciper_id,
  primary_affiliation      = excluded.primary_affiliation,
  primary_affiliation_uuid = excluded.primary_affiliation_uuid,
  raw                      = excluded.raw,
  ingested_at              = excluded.ingested_at;
"""


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------

_ORG_UPSERT_SQL = """
CREATE OR REPLACE TEMP TABLE _orgs AS
  SELECT json FROM read_json_objects(?);

INSERT INTO organizations
  (org_uuid, name, acronym, parent_org_uuid, sciper_unit_id, ror_id, raw, ingested_at)
SELECT
  json_extract_string(json, '$.uuid') AS org_uuid,
  COALESCE(
    json_extract_string(json, '$.metadata."dc.title"[0].value'),
    json_extract_string(json, '$.metadata."organization.legalName"[0].value')
  ) AS name,
  json_extract_string(json, '$.metadata."organization.identifier.acronym"[0].value') AS acronym,
  json_extract_string(json, '$.metadata."cris.virtual.parent-organization"[0].authority') AS parent_org_uuid,
  COALESCE(
    json_extract_string(json, '$.metadata."cris.virtual.unitId"[0].value'),
    json_extract_string(json, '$.metadata."epfl.unitId"[0].value')
  ) AS sciper_unit_id,
  json_extract_string(json, '$.metadata."cris.virtualsource.ror"[0].value') AS ror_id,
  json AS raw,
  CURRENT_TIMESTAMP AS ingested_at
FROM _orgs
WHERE json_extract_string(json, '$.uuid') IS NOT NULL
ON CONFLICT (org_uuid) DO UPDATE SET
  name            = excluded.name,
  acronym         = excluded.acronym,
  parent_org_uuid = excluded.parent_org_uuid,
  sciper_unit_id  = excluded.sciper_unit_id,
  ror_id          = excluded.ror_id,
  raw             = excluded.raw,
  ingested_at     = excluded.ingested_at;
"""


# ---------------------------------------------------------------------------
# Public ingest functions
# ---------------------------------------------------------------------------


def ingest_articles(store: DuckDBStore, items_dir: Path | None = None) -> int:
    items_dir = items_dir or raw_items_dir()
    glob = str(items_dir / "*.json")
    conn = store.connect()
    # Run the three statements in one transaction.
    conn.execute("BEGIN TRANSACTION")
    try:
        for stmt in _ARTICLE_UPSERT_SQL.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            if "read_json_objects(?)" in stmt:
                conn.execute(stmt + ";", [glob])
            else:
                conn.execute(stmt + ";")
        conn.execute(_ARTICLE_PERSONS_SQL)
        conn.execute(_ARTICLE_ORGS_SQL_TEMPLATE.format(unions=_orgs_union_sql()))
        n = conn.execute("SELECT count(*) FROM _items").fetchone()[0]
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    logger.info("ingest_articles: %d", n)
    return int(n)


def ingest_persons(store: DuckDBStore, persons_dir: Path | None = None) -> int:
    persons_dir = persons_dir or raw_persons_dir()
    glob = str(persons_dir / "*.json")
    conn = store.connect()
    conn.execute("BEGIN TRANSACTION")
    try:
        for stmt in _PERSON_UPSERT_SQL.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            if "read_json_objects(?)" in stmt:
                conn.execute(stmt + ";", [glob])
            else:
                conn.execute(stmt + ";")
        n = conn.execute("SELECT count(*) FROM _persons").fetchone()[0]
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    logger.info("ingest_persons: %d", n)
    return int(n)


def ingest_organizations(
    store: DuckDBStore,
    orgs_dir: Path | None = None,
) -> int:
    orgs_dir = orgs_dir or raw_organizations_dir()
    glob = str(orgs_dir / "*.json")
    conn = store.connect()
    conn.execute("BEGIN TRANSACTION")
    try:
        for stmt in _ORG_UPSERT_SQL.strip().split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            if "read_json_objects(?)" in stmt:
                conn.execute(stmt + ";", [glob])
            else:
                conn.execute(stmt + ";")
        n = conn.execute("SELECT count(*) FROM _orgs").fetchone()[0]
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    logger.info("ingest_organizations: %d", n)
    return int(n)


def ingest_links_dump(store: DuckDBStore, dump_path: Path) -> int:
    """Read a `scripts/dump_link_articles.py` output and upsert article_links.

    The heavy dump is a single ~400 MB JSON. Read once with DuckDB's JSON
    loader so the whole `articles` array becomes a row set we can unnest
    in pure SQL.
    """
    dump_path = Path(dump_path)
    if not dump_path.exists():
        return 0

    # Pull only the metadata we need (queries map + articles array) so we
    # don't materialise the full ~400 MB persons/organizations sub-objects.
    raw = json.loads(dump_path.read_text(encoding="utf-8"))
    queries = raw.get("queries", {})
    articles = raw.get("articles", [])

    rows: list[tuple[str, str, str, str]] = []
    for art in articles:
        uuid = art.get("uuid")
        if not uuid:
            continue
        for label in art.get("matched_phrases", []) or []:
            phrase = queries.get(label, "")
            rows.append((uuid, label, phrase, "phrase_match"))
        for label, urls in (art.get("body_urls") or {}).items():
            for url in urls or []:
                rows.append((uuid, label, url, "body_text"))

    if not rows:
        return 0

    conn = store.connect()
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.executemany(
            "INSERT INTO article_links (article_uuid, host_label, url, source) "
            "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            rows,
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    logger.info("ingest_links_dump: %d link rows", len(rows))
    return len(rows)


def ingest_all(store: DuckDBStore, *, links_dump: Path | None = None) -> Dict[str, int]:
    summary = {
        "articles": ingest_articles(store),
        "persons": ingest_persons(store),
        "organizations": ingest_organizations(store),
    }
    if links_dump:
        summary["link_rows"] = ingest_links_dump(store, links_dump)
    return summary
