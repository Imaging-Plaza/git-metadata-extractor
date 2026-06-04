"""DuckDB store for the github_organizations index."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index._github_accounts_base.storage_base import (
    bootstrap_schema,
    count_table,
    fetch_one,
    stream_unembedded,
    upsert_chunk,
)
from src.index.github_organizations.paths import (
    get_github_organizations_paths,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.index.github_organizations.models import OrgRecord

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

ENTITY_TYPE = "organizations"
ID_COLUMN = "login"


class GitHubOrganizationsStore:
    """Thin DuckDB wrapper for the `organizations` schema. `bootstrap()` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> GitHubOrganizationsStore:
        if db_path is None:
            db_path = get_github_organizations_paths().duckdb_path
        store = cls(db_path)
        store.bootstrap()
        return store

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def bootstrap(self) -> None:
        bootstrap_schema(self.connect(), SCHEMA_PATH)

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

    # ---- Upserts ---------------------------------------------------------

    def upsert_organization(self, record: OrgRecord) -> None:
        from src.v2.canonicalization.github import github_org_iri  # noqa: PLC0415

        sql = (
            "INSERT INTO organizations "
            "(login, github_id, node_id, name, description, blog, location, "
            " email, twitter_username, company, public_repos, public_gists, "
            " followers, following, is_verified, has_organization_projects, "
            " has_repository_projects, account_type, avatar_url, html_url, "
            " created_at, updated_at, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "        ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (login) DO UPDATE SET "
            "  github_id = excluded.github_id, node_id = excluded.node_id, "
            "  name = excluded.name, description = excluded.description, "
            "  blog = excluded.blog, location = excluded.location, "
            "  email = excluded.email, "
            "  twitter_username = excluded.twitter_username, "
            "  company = excluded.company, "
            "  public_repos = excluded.public_repos, "
            "  public_gists = excluded.public_gists, "
            "  followers = excluded.followers, "
            "  following = excluded.following, "
            "  is_verified = excluded.is_verified, "
            "  has_organization_projects = excluded.has_organization_projects, "
            "  has_repository_projects = excluded.has_repository_projects, "
            "  account_type = excluded.account_type, "
            "  avatar_url = excluded.avatar_url, "
            "  html_url = excluded.html_url, "
            "  created_at = excluded.created_at, "
            "  updated_at = excluded.updated_at, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                github_org_iri(record.login) or record.login,  # v3.0.0: id is the URL
                record.github_id,
                record.node_id,
                record.name,
                record.description,
                record.blog,
                record.location,
                record.email,
                record.twitter_username,
                record.company,
                record.public_repos,
                record.public_gists,
                record.followers,
                record.following,
                record.is_verified,
                record.has_organization_projects,
                record.has_repository_projects,
                record.account_type,
                record.avatar_url,
                record.html_url,
                record.created_at,
                record.updated_at,
                json.dumps(record.raw, ensure_ascii=False, default=str),
                self._now(),
            ],
        )

    def upsert_chunk(
        self,
        *,
        chunk_id: str,
        entity_type: str,
        entity_id: str,
        chunk_index: int,
        text: str,
        token_count: int,
        vector_id: str,
    ) -> None:
        upsert_chunk(
            self.connect(),
            chunk_id=chunk_id,
            entity_type=entity_type,
            entity_id=entity_id,
            chunk_index=chunk_index,
            text=text,
            token_count=token_count,
            vector_id=vector_id,
        )

    # ---- Reads -----------------------------------------------------------

    def count(self, table: str) -> int:
        return count_table(self.connect(), table)

    def fetch_organization(self, login: str) -> dict[str, Any] | None:
        from src.v2.canonicalization.github import github_org_iri  # noqa: PLC0415

        return fetch_one(
            self.connect(),
            table="organizations",
            id_column=ID_COLUMN,
            id_value=github_org_iri(login) or login,  # accept bare or URL
        )

    def stream_rows_for_embedding(
        self,
        entity_type: str,
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        if entity_type != ENTITY_TYPE:
            message = f"Unknown entity_type: {entity_type!r} (expected {ENTITY_TYPE!r})"
            raise ValueError(message)
        yield from stream_unembedded(
            self.connect(),
            table="organizations",
            id_column=ID_COLUMN,
            entity_type=ENTITY_TYPE,
            limit=limit,
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
