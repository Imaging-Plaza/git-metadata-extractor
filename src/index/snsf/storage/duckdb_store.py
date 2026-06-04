"""DuckDB lifecycle, schema bootstrap, and bulk loaders for the SNSF P3 index.

All ingest goes through DuckDB's native `read_csv_auto` — Python doesn't
iterate the 90 k-grant CSV. This is 50-100× faster than the per-row INSERT
path and avoids the PARALLEL FALSE / STRICT_MODE FALSE dance ROR needed for
multi-line quoted JSON columns. See `.internal/snsf/README.md` for the
column-by-column rationale.
"""

from __future__ import annotations

import datetime as dt
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import duckdb

from src.index.snsf.models import IngestManifest
from src.index.snsf.paths import duckdb_path
from src.v2.canonicalization.snsf import snsf_grant_iri, snsf_grant_iri_sql

if TYPE_CHECKING:
    from collections.abc import Iterator

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# v3.0.0: the grant id is the canonical SNSF grant URL. This SQL fragment
# URL-ifies the bare integer `GrantNumber` from the CSV (and is reused by the
# bootstrap migration) so every `grant_number` PK/FK holds the URL.
_GRANT_URL_SQL = snsf_grant_iri_sql("GrantNumber")
_GRANT_BASE = "https://data.snf.ch/grants/grant/"

# Output tables whose `grant_number` FK must be migrated alongside `grants`.
_GRANT_FK_TABLES = (
    "output_publications",
    "output_academic_events",
    "output_collaborations",
    "output_datasets",
    "output_knowledge_transfers",
    "output_public_communications",
    "output_use_inspired",
    "scope_records",
)

# Per-role grant-number JSON-array columns in `persons` (lists of grant ids).
_PERSON_GRANT_COLS = (
    "responsible_applicant_grants",
    "co_applicant_grants",
    "project_partner_grants",
    "practice_partner_grants",
    "employee_grants",
    "contact_person_grants",
    "applicant_abroad_grants",
)


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class SnsfStore:
    """Thin wrapper around DuckDB tuned for the SNSF schema."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> SnsfStore:
        if db_path is None:
            db_path = duckdb_path()
        store = cls(db_path)
        store.bootstrap()
        return store

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def bootstrap(self) -> None:
        conn = self.connect()
        conn.execute(_load_schema_sql())
        # Promote `output_publications.doi` to canonical
        # `https://doi.org/<bare>`. Idempotent.
        from src.index._shared.doi import (  # noqa: PLC0415
            migrate_doi_column_to_url,
        )

        migrate_doi_column_to_url(conn, table="output_publications", column="doi")
        self._migrate_grant_ids_to_url(conn)

    @staticmethod
    def _migrate_grant_ids_to_url(conn: duckdb.DuckDBPyConnection) -> None:
        """v3.0.0: promote the integer `grant_number` PK/FKs (and the per-role
        grant-number JSON arrays in `persons`) to the canonical grant URL.

        Gated on the pre-v3 schema (`grants.grant_number` still INTEGER) so the
        whole migration runs exactly once: after it, every `grant_number` is
        VARCHAR and re-runs are no-ops. Fresh DBs (TEXT from schema.sql) skip.

        DuckDB can't ``ALTER COLUMN ... TYPE`` a PRIMARY KEY column (``grants``,
        ``scope_records``), so each affected table is rebuilt: snapshot into a
        TEMP with the URL-transformed ``grant_number``, drop the original
        (releasing its index names), recreate the fresh TEXT schema, then
        re-insert ``BY NAME``. ``persons`` keeps its columns — only its JSON
        grant-arrays are rewritten in place.
        """
        row = conn.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'grants' AND column_name = 'grant_number'",
        ).fetchone()
        if row is None or "INT" not in str(row[0]).upper():
            return  # already migrated (TEXT/VARCHAR) or table absent

        existing = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'",
            ).fetchall()
        }
        url_sql = snsf_grant_iri_sql("grant_number")
        tables = [t for t in ("grants", *_GRANT_FK_TABLES) if t in existing]
        for table in tables:
            conn.execute(
                f"CREATE TEMP TABLE _mig_{table} AS "  # noqa: S608
                f"SELECT * REPLACE ({url_sql} AS grant_number) FROM {table}",
            )
            conn.execute(f"DROP TABLE {table}")  # noqa: S608 — also drops its indexes
        conn.execute(_load_schema_sql())  # recreate fresh TEXT tables + indexes
        for table in tables:
            conn.execute(
                f"INSERT INTO {table} BY NAME SELECT * FROM _mig_{table}",  # noqa: S608
            )
            conn.execute(f"DROP TABLE _mig_{table}")  # noqa: S608
        # persons keeps its schema; only its JSON arrays of bare integers
        # become arrays of grant URLs.
        if "persons" not in existing:
            return
        for col in _PERSON_GRANT_COLS:
            conn.execute(
                f"UPDATE persons SET {col} = TO_JSON(LIST_TRANSFORM("  # noqa: S608
                f"CAST({col} AS BIGINT[]), n -> '{_GRANT_BASE}' || n)) "
                f"WHERE {col} IS NOT NULL",
            )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def read_only(self) -> Iterator[duckdb.DuckDBPyConnection]:
        ro = duckdb.connect(str(self.db_path), read_only=True)
        try:
            yield ro
        finally:
            ro.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        conn = self.connect()
        conn.execute("BEGIN TRANSACTION")
        try:
            yield
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    # ---- Bulk loaders (CSV → tables) -------------------------------------

    def load_grants(self, csv_path: Path) -> int:
        """Replace the `grants` table from `grants_with_abstracts.csv`.

        Uses DuckDB `read_csv_auto` with a multi-line-aware reader so the
        embedded newlines in `Abstract` / `LaySummary*` are handled correctly.
        Total runtime on the full 90 k-grant CSV (~424 MB): ~10 s.
        """
        if not csv_path.exists():
            msg = f"grants CSV not found: {csv_path}"
            raise FileNotFoundError(msg)
        conn = self.connect()
        with self.transaction():
            conn.execute("DELETE FROM grants")
            conn.execute(
                """
                INSERT INTO grants (
                    grant_number, grant_number_string, title, title_english,
                    responsible_applicant,
                    funding_instrument, funding_instrument_reporting, funding_instrument_l1,
                    institute, institute_city, institute_country,
                    research_institution, research_institution_type,
                    main_discipline, main_discipline_number,
                    main_discipline_l1, main_discipline_l2, all_disciplines,
                    main_field_of_research, main_field_of_research_la,
                    main_field_of_research_lb, all_field_of_researchs,
                    start_date, end_date, amount_granted, keywords, abstract,
                    lay_summary_lead_en, lay_summary_en,
                    lay_summary_lead_de, lay_summary_de,
                    lay_summary_lead_fr, lay_summary_fr,
                    lay_summary_lead_it, lay_summary_it,
                    state, call_full_title, call_end_date, call_decision_year
                )
                SELECT
                    """ + _GRANT_URL_SQL + """, GrantNumberString, Title, TitleEnglish,
                    ResponsibleApplicantName,
                    FundingInstrumentPublished, FundingInstrumentReporting, FundingInstrumentLevel1,
                    Institute, InstituteCity, InstituteCountry,
                    ResearchInstitution, ResearchInstitutionType,
                    MainDiscipline, MainDisciplineNumber,
                    MainDiscipline_Level1, MainDiscipline_Level2, AllDisciplines,
                    MainFieldOfResearch, MainFieldOfResearch_LevelA,
                    MainFieldOfResearch_LevelB, AllFieldOfResearchs,
                    EffectiveGrantStartDate, EffectiveGrantEndDate,
                    AmountGrantedAllSets, Keywords, Abstract,
                    LaySummaryLead_En, LaySummary_En,
                    LaySummaryLead_De, LaySummary_De,
                    LaySummaryLead_Fr, LaySummary_Fr,
                    LaySummaryLead_It, LaySummary_It,
                    State, CallFullTitle, CallEndDate, CallDecisionYear
                FROM read_csv_auto(?, delim=';', header=true, sample_size=-1)
                """,
                [str(csv_path)],
            )
        return self.count_grants()

    def load_persons(self, csv_path: Path) -> int:
        """Replace the `persons` table from `persons.csv`.

        The role-specific grant lists (e.g. `EmployeeGrantNumber`) are
        semicolon-delimited TEXT in the source; we split + JSON-encode them
        in SQL so callers get `json_each(employee_grants)` ergonomics.
        Empty/NULL source → NULL JSON column.
        """
        if not csv_path.exists():
            msg = f"persons CSV not found: {csv_path}"
            raise FileNotFoundError(msg)

        # Inline DuckDB expression that turns "108806;125710;134273" → JSON [108806,125710,134273].
        # NULL input → NULL output. Empty string → NULL.
        # Some columns in persons.csv only ever contain a single grant_number per row,
        # so DuckDB auto-detects them as BIGINT — explicit CAST(... AS VARCHAR) is needed
        # before TRIM/STRING_SPLIT to keep the SQL valid for both BIGINT and VARCHAR types.
        # v3.0.0: the per-role grant lists hold canonical grant URLs, not bare
        # integers. Non-numeric tokens map to NULL (as the old TRY_CAST did).
        def _split(col: str) -> str:
            return (
                f"CASE WHEN {col} IS NULL "
                f"OR LENGTH(TRIM(CAST({col} AS VARCHAR))) = 0 THEN NULL "
                f"ELSE TO_JSON(LIST_TRANSFORM("
                f"STRING_SPLIT(CAST({col} AS VARCHAR), ';'), "
                f"x -> CASE WHEN regexp_full_match(TRIM(x), '\\d+') "
                f"THEN '{_GRANT_BASE}' || TRIM(x) ELSE NULL END)) END"
            )

        conn = self.connect()
        with self.transaction():
            conn.execute("DELETE FROM persons")
            conn.execute(
                f"""
                INSERT INTO persons (
                    person_number, first_name, last_name,
                    institute, institute_place, institute_country,
                    research_institution, research_institution_type, orcid,
                    responsible_applicant_grants, co_applicant_grants,
                    project_partner_grants, practice_partner_grants,
                    employee_grants, contact_person_grants, applicant_abroad_grants,
                    person_grant_discipline, person_grant_keywords
                )
                SELECT
                    PersonNumber, FirstName, LastName,
                    Institute, InstitutePlace, InstituteCountry,
                    ResearchInstitution, ResearchInstitutionType, ORCID,
                    {_split("ResponsibleApplicantGrantNumber")},
                    {_split("CoApplicantGrantNumber")},
                    {_split("ProjectPartnerGrantNumber")},
                    {_split("PracticePartnerGrantNumber")},
                    {_split("EmployeeGrantNumber")},
                    {_split("ContactPersonGrantNumber")},
                    {_split("ApplicantAbroadGrantNumber")},
                    Person_Grant_Discipline, Person_Grant_Keywords
                FROM read_csv_auto(?, delim=';', header=true, sample_size=-1)
                """,  # noqa: S608 — column expressions are fixed strings, not user input
                [str(csv_path)],
            )
        return self.count_persons()

    def load_disciplines(self, csv_path: Path) -> int:
        """Replace the `discipline_taxonomy` table from `SNF_field_of_research_disciplines.csv`."""
        if not csv_path.exists():
            msg = f"disciplines CSV not found: {csv_path}"
            raise FileNotFoundError(msg)
        conn = self.connect()
        with self.transaction():
            conn.execute("DELETE FROM discipline_taxonomy")
            conn.execute(
                """
                INSERT INTO discipline_taxonomy (
                    mapping_direction, field_of_research_number, field_of_research,
                    snf_discipline_number, snf_discipline
                )
                SELECT
                    "Mapping direction",
                    "Field of research #",
                    "Field of research",
                    "mySNF discipline #",
                    "mySNF discipline"
                FROM read_csv_auto(?, delim=';', header=true, sample_size=-1)
                """,
                [str(csv_path)],
            )
        return self.count_disciplines()

    # ---- Output tables -----------------------------------------------------
    #
    # All seven `output_data_*.csv` files load via DuckDB `read_csv_auto` into
    # a `read_only=False` connection. Source CSVs all share the same shape
    # (one CSV per output type, primary key = source UUID, FK = GrantNumber).

    def load_output_publications(self, csv_path: Path) -> int:
        return self._replace_output_table(
            csv_path,
            table="output_publications",
            cols=(
                "publication_id, grant_number, peer_review_status, type, title, "
                "author, state, year, isbn, doi, import_source, "
                "open_access_yes_no, open_access_status, url, "
                "publication_title, publisher, editor, volume, issue_number, "
                "first_page_number, last_page_number, proceeding_location, abstract"
            ),
            select=(
                "ScientificPublicationId, GrantNumber, "
                "ScientificPublication_PeerReviewStatus, ScientificPublication_Type, "
                "ScientificPublication_Title, ScientificPublication_Author, "
                "ScientificPublication_State, "
                "TRY_CAST(ScientificPublication_Year AS INTEGER), "
                "ScientificPublication_ISBN, "
                # Promote bare DOIs to canonical `https://doi.org/<doi>` at
                # CSV-load time so readers don't have to wait for the next
                # `bootstrap()` to converge. CASE handles NULL / already-URL
                # rows untouched.
                "CASE "
                "  WHEN ScientificPublication_DOI IS NULL "
                "       OR TRIM(ScientificPublication_DOI) = '' THEN NULL "
                "  WHEN ScientificPublication_DOI LIKE 'https://doi.org/%' "
                "       THEN ScientificPublication_DOI "
                "  WHEN ScientificPublication_DOI LIKE 'https://dx.doi.org/%' "
                "       THEN 'https://doi.org/' || "
                "            SUBSTRING(ScientificPublication_DOI, "
                "                      LENGTH('https://dx.doi.org/') + 1) "
                "  WHEN LOWER(ScientificPublication_DOI) LIKE 'doi:%' "
                "       THEN 'https://doi.org/' || "
                "            SUBSTRING(ScientificPublication_DOI, 5) "
                "  ELSE 'https://doi.org/' || ScientificPublication_DOI "
                "END, "
                "ScientificPublication_ImportSource, "
                "TRY_CAST(ScientificPublication_OpenAccessStatusYesNo AS INTEGER), "
                "ScientificPublication_OpenAccessStatus, ScientificPublication_Url, "
                "ScientificPublication_PublicationTitle, ScientificPublication_Publisher, "
                "ScientificPublication_Editor, "
                "CAST(ScientificPublication_Volume AS VARCHAR), "
                "CAST(ScientificPublication_IssueNumber AS VARCHAR), "
                "CAST(ScientificPublication_FirstPageNumber AS VARCHAR), "
                "CAST(ScientificPublication_LastPageNumber AS VARCHAR), "
                "ScientificPublication_ProceedingLocation, "
                "ScientificPublication_Abstract"
            ),
        )

    def load_output_academic_events(self, csv_path: Path) -> int:
        return self._replace_output_table(
            csv_path,
            table="output_academic_events",
            cols=(
                "event_id, grant_number, type, event, contribution_title, "
                "date, involved_person, url, place"
            ),
            select=(
                "AcademicEventId, GrantNumber, AcademicEvent_Type, AcademicEvent_Event, "
                "AcademicEvent_ContributionTitle, AcademicEvent_Date, "
                "AcademicEvent_InvolvedPerson, AcademicEvent_Url, AcademicEvent_Place"
            ),
        )

    def load_output_collaborations(self, csv_path: Path) -> int:
        return self._replace_output_table(
            csv_path,
            table="output_collaborations",
            cols=(
                "collaboration_id, grant_number, research_group, type, "
                "country, start_date, end_date"
            ),
            select=(
                "CollaborationId, GrantNumber, Collaboration_ResearchGroup, "
                "Collaboration_Type, Collaboration_Country, "
                "EffectiveGrantStartDate, EffectiveGrantEndDate"
            ),
        )

    def load_output_datasets(self, csv_path: Path) -> int:
        return self._replace_output_table(
            csv_path,
            table="output_datasets",
            cols=(
                "dataset_id, grant_number, title, author, persistent_identifier, "
                "repository_name, repository_link, publication_date, abstract"
            ),
            select=(
                "DataSetId, GrantNumber, DataSet_Title, DataSet_Author, "
                "DataSet_PersistentIdentifier_PID, DataSet_RepositoryName, "
                "DataSet_RepositoryLink, DataSet_PublicationDate, DataSet_Abstract"
            ),
        )

    def load_output_knowledge_transfers(self, csv_path: Path) -> int:
        return self._replace_output_table(
            csv_path,
            table="output_knowledge_transfers",
            cols=(
                "event_id, grant_number, type, event, date, "
                "involved_person, url, place, target_group"
            ),
            select=(
                "KnowledgeTransferEventId, GrantNumber, KnowledgeTransferEvent_Type, "
                "KnowledgeTransferEvent_Event, KnowledgeTransferEvent_Date, "
                "KnowledgeTransferEvent_InvolvedPerson, KnowledgeTransferEvent_Url, "
                "KnowledgeTransferEvent_Place, KnowledgeTransferEvent_TargetGroup"
            ),
        )

    def load_output_public_communications(self, csv_path: Path) -> int:
        return self._replace_output_table(
            csv_path,
            table="output_public_communications",
            cols=(
                "communication_id, grant_number, type, title, description, "
                "year, url, region"
            ),
            select=(
                "PublicCommunicationId, GrantNumber, PublicCommunication_Type, "
                "PublicCommunication_Title, PublicCommunication_Description, "
                "TRY_CAST(PublicCommunication_Year AS INTEGER), "
                "PublicCommunication_Url, PublicCommunication_Region"
            ),
        )

    def load_output_use_inspired(self, csv_path: Path) -> int:
        return self._replace_output_table(
            csv_path,
            table="output_use_inspired",
            cols=(
                "use_inspired_id, grant_number, type, title, url, year, "
                "priority_date, patent_number, patent_status, patent_decision_date, "
                "inventor, owner, patent_owner_description, comment, "
                "reviewer_activity_type, license_type"
            ),
            select=(
                "UseInspiredId, GrantNumber, UseInspired_Type, UseInspired_Title, "
                "UseInspired_Url, "
                "TRY_CAST(UseInspired_Year AS INTEGER), "
                "UseInspired_PriorityDate, UseInspired_PatentNumber, "
                "UseInspired_PatentStatus, UseInspired_PatentDecisionDate, "
                "UseInspired_Inventor, UseInspired_Owner, "
                "UseInspired_PatentOwnerDescription, UseInspired_Comment, "
                "UseInspired_ReviewerActivityType, UseInspired_LicenseType"
            ),
        )

    def _replace_output_table(
        self, csv_path: Path, *, table: str, cols: str, select: str,
    ) -> int:
        if not csv_path.exists():
            msg = f"output CSV not found: {csv_path}"
            raise FileNotFoundError(msg)
        # v3.0.0: the `grant_number` FK is the canonical grant URL. The output
        # CSVs all carry a bare-integer `GrantNumber` column; URL-ify it.
        select = select.replace("GrantNumber", _GRANT_URL_SQL, 1)
        conn = self.connect()
        with self.transaction():
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 — table is a fixed string
            conn.execute(
                f"INSERT INTO {table} ({cols}) SELECT {select} "  # noqa: S608
                "FROM read_csv_auto(?, delim=';', header=true, sample_size=-1)",
                [str(csv_path)],
            )
        result = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
        return int(result[0]) if result else 0

    # ---- Scope + manifest --------------------------------------------------

    def replace_scope_records_by_filter(
        self, scope_mode: str, where_clause: str, params: Optional[list[Any]] = None,
    ) -> int:
        """Re-derive `scope_records` for one scope from a SQL WHERE on `grants`.

        Example:
            store.replace_scope_records_by_filter(
                "epfl",
                "research_institution = ?",
                ["EPF Lausanne – EPFL"],
            )
        """
        params = params or []
        conn = self.connect()
        with self.transaction():
            conn.execute(
                "DELETE FROM scope_records WHERE scope_mode = ?", [scope_mode],
            )
            conn.execute(
                "INSERT INTO scope_records (scope_mode, grant_number) "
                f"SELECT ?, grant_number FROM grants WHERE {where_clause}",  # noqa: S608 — caller controls
                [scope_mode, *params],
            )
        return self.count_scope_records(scope_mode)

    def set_manifest(self, manifest: IngestManifest) -> None:
        sql = (
            "INSERT INTO manifests "
            "(scope_mode, record_count, snapshot_iso, source_dir, built_at_iso) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (scope_mode) DO UPDATE SET "
            "record_count = excluded.record_count, "
            "snapshot_iso = excluded.snapshot_iso, "
            "source_dir   = excluded.source_dir, "
            "built_at_iso = excluded.built_at_iso"
        )
        self.connect().execute(
            sql,
            [
                manifest.scope_mode,
                manifest.record_count,
                manifest.snapshot_iso or _now_iso(),
                manifest.source_dir,
                manifest.built_at_iso or _now_iso(),
            ],
        )

    # ---- Reads -------------------------------------------------------------

    def count_grants(self) -> int:
        result = self.connect().execute("SELECT count(*) FROM grants").fetchone()
        return int(result[0]) if result else 0

    def count_persons(self) -> int:
        result = self.connect().execute("SELECT count(*) FROM persons").fetchone()
        return int(result[0]) if result else 0

    def count_disciplines(self) -> int:
        result = self.connect().execute(
            "SELECT count(*) FROM discipline_taxonomy",
        ).fetchone()
        return int(result[0]) if result else 0

    def count_scope_records(self, scope_mode: str) -> int:
        result = self.connect().execute(
            "SELECT count(*) FROM scope_records WHERE scope_mode = ?",
            [scope_mode],
        ).fetchone()
        return int(result[0]) if result else 0

    def fetch_grant(self, grant_number: object) -> Optional[dict[str, Any]]:
        # Accept a bare int / numeric string or the canonical grant URL.
        cur = self.connect().execute(
            "SELECT * FROM grants WHERE grant_number = ?",
            [snsf_grant_iri(grant_number) or grant_number],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def fetch_manifest(self, scope_mode: str) -> Optional[dict[str, Any]]:
        cur = self.connect().execute(
            "SELECT * FROM manifests WHERE scope_mode = ?", [scope_mode],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


__all__ = ["SnsfStore"]
