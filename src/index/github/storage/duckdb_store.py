"""DuckDB lifecycle, schema bootstrap, and upsert helpers for the GitHub index."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from src.index.github.paths import get_github_paths

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.index.github.models import RepoRecord

LOGGER = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

EMBEDDABLE_ENTITY_TYPES = {"repos"}


def _load_schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


class GitHubStore:
    """Thin DuckDB wrapper for the GitHub schema. `bootstrap()` is idempotent."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @classmethod
    def open(cls, db_path: Path | None = None) -> GitHubStore:
        if db_path is None:
            db_path = get_github_paths().duckdb_path
        store = cls(db_path)
        store.bootstrap()
        return store

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def bootstrap(self) -> None:
        self.connect().execute(_load_schema_sql())

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

    def upsert_repo(self, record: RepoRecord) -> None:
        sql = (
            "INSERT INTO repos "
            "(repo_id, owner, name, default_branch, description, homepage, "
            " primary_language, languages, topics, license_spdx, is_fork, "
            " is_archived, is_private, stargazers_count, forks_count, "
            " watchers_count, open_issues_count, size_kb, created_at, "
            " pushed_at, readme_path, contributors, raw, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "        ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (repo_id) DO UPDATE SET "
            "  owner = excluded.owner, name = excluded.name, "
            "  default_branch = excluded.default_branch, "
            "  description = excluded.description, "
            "  homepage = excluded.homepage, "
            "  primary_language = excluded.primary_language, "
            "  languages = excluded.languages, topics = excluded.topics, "
            "  license_spdx = excluded.license_spdx, is_fork = excluded.is_fork, "
            "  is_archived = excluded.is_archived, is_private = excluded.is_private, "
            "  stargazers_count = excluded.stargazers_count, "
            "  forks_count = excluded.forks_count, "
            "  watchers_count = excluded.watchers_count, "
            "  open_issues_count = excluded.open_issues_count, "
            "  size_kb = excluded.size_kb, "
            "  created_at = excluded.created_at, "
            "  pushed_at = excluded.pushed_at, "
            "  readme_path = excluded.readme_path, "
            "  contributors = excluded.contributors, "
            "  raw = excluded.raw, ingested_at = excluded.ingested_at"
        )
        self.connect().execute(
            sql,
            [
                record.repo_id,
                record.owner,
                record.name,
                record.default_branch,
                record.description,
                record.homepage,
                record.primary_language,
                json.dumps(record.languages, ensure_ascii=False),
                json.dumps(record.topics, ensure_ascii=False),
                record.license_spdx,
                record.is_fork,
                record.is_archived,
                record.is_private,
                record.stargazers_count,
                record.forks_count,
                record.watchers_count,
                record.open_issues_count,
                record.size_kb,
                record.created_at,
                record.pushed_at,
                record.readme_path,
                json.dumps([c.model_dump() for c in record.contributors], ensure_ascii=False),
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
        self.connect().execute(
            "INSERT INTO chunks "
            "(chunk_id, entity_type, entity_id, chunk_index, text, "
            "token_count, vector_id) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (chunk_id) DO UPDATE SET "
            "text = excluded.text, token_count = excluded.token_count, "
            "vector_id = excluded.vector_id, embedded_at = now()",
            [chunk_id, entity_type, entity_id, chunk_index, text, token_count, vector_id],
        )

    # ---- Reads -----------------------------------------------------------

    def count(self, table: str) -> int:
        result = self.connect().execute(f"SELECT count(*) FROM {table}").fetchone()
        return int(result[0]) if result else 0

    def fetch_repo(self, repo_id: str) -> dict[str, Any] | None:
        cur = self.connect().execute(
            "SELECT * FROM repos WHERE repo_id = ?",
            [repo_id],
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def stream_rows_for_embedding(
        self,
        entity_type: str,
        *,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield repo rows that need embedding (no chunks yet)."""
        if entity_type not in EMBEDDABLE_ENTITY_TYPES:
            message = f"Unknown entity_type: {entity_type}"
            raise ValueError(message)
        sql = (
            "SELECT t.* FROM repos t "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM chunks c "
            "  WHERE c.entity_type = ? AND c.entity_id = t.repo_id"
            ")"
        )
        params: list[Any] = [entity_type]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cur = self.connect().execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        for row in rows:
            yield dict(zip(cols, row, strict=False))

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=timezone.utc).isoformat()
