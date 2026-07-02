"""SnsfGrantsProvider — thin read-only facade over the SNSF DuckDB store.

Used by the LLM agent tool factories in
``src.v2.agents.llm.agent_tools.snsf_grants``.

The provider is cheap to construct (no DB opened at init) and opens a
read-only DuckDB connection only for the duration of each method call, so
it is safe to run alongside a concurrent writer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from open_pulse_sources.index.snsf.facet_query import GrantFilters

logger = logging.getLogger(__name__)


class _ROStore:
    """Minimal shim that exposes ``connect()`` backed by a read-only DuckDB
    connection.  ``query_grants`` and ``facet_counts`` only call
    ``store.connect()`` so this is sufficient.
    """

    def __init__(self, conn: Any) -> None:  # conn: duckdb.DuckDBPyConnection
        self._conn = conn

    def connect(self) -> Any:
        return self._conn


class SnsfGrantsProvider:
    """Read-only query provider for the SNSF P3 DuckDB store.

    Parameters
    ----------
    store_path:
        Path to the ``snsf.duckdb`` file.  Defaults to the canonical path
        returned by :func:`open_pulse_sources.index.snsf.paths.duckdb_path`.  The file is
        **not** opened at construction time.
    """

    def __init__(self, store_path: Path | None = None) -> None:
        if store_path is None:
            from open_pulse_sources.index.snsf.paths import duckdb_path  # noqa: PLC0415

            store_path = duckdb_path()
        self._store_path = store_path

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def search(
        self,
        filters: GrantFilters,
        *,
        text: str | None = None,
        sort: str = "start_date_desc",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Faceted + free-text grant search.

        Returns the thin result rows (grant_number, title, state, dates,
        amount, output counts …) produced by :func:`query_grants`.  Returns
        ``[]`` if the store file is absent or unreadable.
        """
        if not self._store_path.exists():
            return []
        try:
            import duckdb  # noqa: PLC0415

            from open_pulse_sources.index.snsf.facet_query import query_grants  # noqa: PLC0415

            with duckdb.connect(str(self._store_path), read_only=True) as conn:
                store = _ROStore(conn)
                result = query_grants(store, filters, text=text, sort=sort, limit=limit)
                return result["results"]
        except Exception:
            logger.exception("snsf_grants.search failed; returning []")
            return []

    def facets(
        self,
        filters: GrantFilters,
        *,
        text: str | None = None,
    ) -> dict[str, Any]:
        """Facet counts (excluded-self semantics) for the current filter set.

        Returns ``{}`` if the store file is absent or unreadable.
        """
        if not self._store_path.exists():
            return {}
        try:
            import duckdb  # noqa: PLC0415

            from open_pulse_sources.index.snsf.facet_query import facet_counts  # noqa: PLC0415

            with duckdb.connect(str(self._store_path), read_only=True) as conn:
                store = _ROStore(conn)
                return facet_counts(store, filters, text=text)
        except Exception:
            logger.exception("snsf_grants.facets failed; returning {}")
            return {}

    def fetch(self, grant_number: str) -> dict[str, Any] | None:
        """Fetch the full grant row (incl. abstract / lay summaries).

        Accepts the canonical grant URL or a bare integer string / int.
        Returns ``None`` if the store file is absent, the grant is not found,
        or the store is unreadable.
        """
        if not self._store_path.exists():
            return None
        try:
            import duckdb  # noqa: PLC0415

            from src.v2.canonicalization.snsf import snsf_grant_iri  # noqa: PLC0415

            canonical = snsf_grant_iri(grant_number) or grant_number
            with duckdb.connect(str(self._store_path), read_only=True) as conn:
                cur = conn.execute(
                    "SELECT * FROM grants WHERE grant_number = ?",
                    [canonical],
                )
                row = cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
                result: dict[str, Any] = {}
                for col, val in zip(cols, row, strict=False):
                    result[col] = val.isoformat() if hasattr(val, "isoformat") else val
                return result
        except Exception:
            logger.exception("snsf_grants.fetch failed; returning None")
            return None


__all__ = ["SnsfGrantsProvider"]
