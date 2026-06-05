"""Faceted SQL query over the SNSF DuckDB store.

Public API:
    GrantFilters  -- dataclass with all supported filter fields.
    query_grants  -- execute a faceted + free-text grant search, return rows + total.
    facet_counts  -- per-facet value->count with excluded-self semantics.

All SQL is fully parameterised; user-supplied values are *never* interpolated
into SQL strings.  The only things embedded in the SQL text are column names
and table names -- all drawn from fixed module-level constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.index.snsf.storage.duckdb_store import SnsfStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Facets backed by a direct column on `grants`
_COL_FACETS: list[str] = [
    "funding_instrument",
    "research_institution",
    "state",
    "main_discipline",
    "main_field_of_research",
    "call_decision_year",
]

# Canonical output-type name -> grant_output_counts column
_OUTPUT_COL: dict[str, str] = {
    "publications": "n_publications",
    "datasets": "n_datasets",
    "collaborations": "n_collaborations",
    "academic_events": "n_academic_events",
    "knowledge_transfers": "n_knowledge_transfers",
    "public_communications": "n_public_communications",
    "use_inspired": "n_use_inspired",
}

# sort key -> ORDER BY expression (column references are fixed, not user-supplied)
_SORT_MAP: dict[str, str] = {
    "start_date_desc": "g.start_date DESC NULLS LAST",
    "start_date_asc": "g.start_date ASC NULLS LAST",
    "amount_desc": "g.amount_granted DESC NULLS LAST",
    "amount_asc": "g.amount_granted ASC NULLS LAST",
}
_DEFAULT_SORT = "g.start_date DESC NULLS LAST"

# The LEFT JOIN clause shared by query_grants and facet_counts
_JOIN = "LEFT JOIN grant_output_counts oc ON oc.grant_number = g.grant_number"


# ---------------------------------------------------------------------------
# GrantFilters
# ---------------------------------------------------------------------------


@dataclass
class GrantFilters:
    """All supported facet filter fields. All optional (default None = inactive)."""

    funding_instrument: list[str] | None = field(default=None)
    research_institution: list[str] | None = field(default=None)
    state: list[str] | None = field(default=None)
    main_discipline: list[str] | None = field(default=None)
    main_field_of_research: list[str] | None = field(default=None)
    call_decision_year: list[int] | None = field(default=None)
    country: list[str] | None = field(default=None)
    person_number: int | None = field(default=None)
    person_role: str | None = field(default=None)
    has_output: list[str] | None = field(default=None)
    start_from: str | None = field(default=None)
    start_to: str | None = field(default=None)
    end_from: str | None = field(default=None)
    end_to: str | None = field(default=None)


# ---------------------------------------------------------------------------
# _build_where helpers
# ---------------------------------------------------------------------------


def _add_col_facets(
    filters: GrantFilters,
    exclude: str | None,
    predicates: list[str],
    params: list[Any],
) -> None:
    """Append IN-list predicates for the column-backed facets."""
    for col in _COL_FACETS:
        if col == exclude:
            continue
        values: list[Any] | None = getattr(filters, col)
        if not values:
            continue
        placeholders = ", ".join(["?"] * len(values))
        # col is a fixed string from _COL_FACETS — not user-supplied
        predicates.append(f"g.{col} IN ({placeholders})")
        params.extend(values)


def _add_country_predicate(
    filters: GrantFilters,
    exclude: str | None,
    predicates: list[str],
    params: list[Any],
) -> None:
    """Append EXISTS predicate for the country facet."""
    if exclude == "country":
        return
    country_vals = filters.country
    if not country_vals:
        return
    placeholders = ", ".join(["?"] * len(country_vals))
    # All values are parameterised; {placeholders} is only ?-markers, not user input
    pred = (
        "EXISTS ("  # noqa: S608
        "SELECT 1 FROM grant_countries gc "
        "WHERE gc.grant_number = g.grant_number "
        f"AND gc.country IN ({placeholders})"
        ")"
    )
    predicates.append(pred)
    params.extend(country_vals)


def _add_person_predicate(
    filters: GrantFilters,
    predicates: list[str],
    params: list[Any],
) -> None:
    """Append EXISTS predicate for the person_number (+ optional role) facet."""
    if filters.person_number is None:
        return
    if filters.person_role:
        predicates.append(
            "EXISTS ("
            "SELECT 1 FROM grant_persons gp "
            "WHERE gp.grant_number = g.grant_number "
            "AND gp.person_number = ? "
            "AND gp.role = ?"
            ")",
        )
        params.extend([filters.person_number, filters.person_role])
    else:
        predicates.append(
            "EXISTS ("
            "SELECT 1 FROM grant_persons gp "
            "WHERE gp.grant_number = g.grant_number "
            "AND gp.person_number = ?"
            ")",
        )
        params.append(filters.person_number)


def _add_output_predicates(
    filters: GrantFilters,
    predicates: list[str],
) -> None:
    """Append oc.n_<type> > 0 predicates for the has_output filter."""
    if not filters.has_output:
        return
    for output_name in filters.has_output:
        col_name = _OUTPUT_COL.get(output_name)
        if col_name:
            # col_name is from _OUTPUT_COL — fixed constant, not user input
            predicates.append(f"oc.{col_name} > 0")


def _add_date_predicates(
    filters: GrantFilters,
    predicates: list[str],
    params: list[Any],
) -> None:
    """Append start/end date range predicates."""
    if filters.start_from is not None:
        predicates.append("g.start_date >= ?")
        params.append(filters.start_from)
    if filters.start_to is not None:
        predicates.append("g.start_date <= ?")
        params.append(filters.start_to)
    if filters.end_from is not None:
        predicates.append("g.end_date >= ?")
        params.append(filters.end_from)
    if filters.end_to is not None:
        predicates.append("g.end_date <= ?")
        params.append(filters.end_to)


def _add_text_predicate(
    text: str | None,
    predicates: list[str],
    params: list[Any],
) -> None:
    """Append ILIKE free-text predicate across title / title_english / abstract / keywords."""
    if not text:
        return
    like_val = f"%{text}%"
    predicates.append(
        "(g.title ILIKE ? OR g.title_english ILIKE ? "
        "OR g.abstract ILIKE ? OR g.keywords ILIKE ?)",
    )
    params.extend([like_val, like_val, like_val, like_val])


# ---------------------------------------------------------------------------
# _build_where — private
# ---------------------------------------------------------------------------


def _build_where(
    filters: GrantFilters,
    text: str | None,
    *,
    exclude: str | None = None,
) -> tuple[str, list[Any]]:
    """Build a SQL WHERE-clause body (without the word WHERE) + ordered params.

    Parameters
    ----------
    filters:
        The active filter set.
    text:
        Optional free-text search string (matched with ILIKE).
    exclude:
        A facet name to omit from the predicates (used by ``facet_counts`` for
        the excluded-self semantics).

    Returns
    -------
    (clause, params)
        ``clause`` is a string placed after ``WHERE``.
        ``params`` is the ordered list of ``?`` substitution values.
    """
    predicates: list[str] = []
    params: list[Any] = []

    _add_col_facets(filters, exclude, predicates, params)
    _add_country_predicate(filters, exclude, predicates, params)
    _add_person_predicate(filters, predicates, params)
    _add_output_predicates(filters, predicates)
    _add_date_predicates(filters, predicates, params)
    _add_text_predicate(text, predicates, params)

    if not predicates:
        return "1=1", []
    return " AND ".join(predicates), params


# ---------------------------------------------------------------------------
# query_grants
# ---------------------------------------------------------------------------


def _row_to_dict(
    description: list[Any],
    row: tuple[Any, ...],
) -> dict[str, Any]:
    """Convert a DuckDB result row to a dict, serialising dates to ISO strings."""
    result: dict[str, Any] = {}
    for (col_name, *_), value in zip(description, row, strict=False):
        if hasattr(value, "isoformat"):
            result[col_name] = value.isoformat()
        else:
            result[col_name] = value
    return result


def query_grants(  # noqa: PLR0913
    store: SnsfStore,
    filters: GrantFilters,
    *,
    text: str | None = None,
    sort: str = "start_date_desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Execute a faceted grant search.

    Returns
    -------
    {"total": int, "results": [row_dict, ...]}
        ``total`` is the count without limit/offset.
        Each ``row_dict`` contains the columns listed in the SELECT plus
        ``n_publications``, ``n_datasets``, ``n_collaborations``.
    """
    where, params = _build_where(filters, text)
    order = _SORT_MAP.get(sort, _DEFAULT_SORT)

    conn = store.connect()

    # total count (no limit/offset) — where embeds only ?-parameterised predicates
    count_sql = (
        f"SELECT count(*) FROM grants g {_JOIN} WHERE {where}"  # noqa: S608
    )
    total_row = conn.execute(count_sql, params).fetchone()
    total = int(total_row[0]) if total_row else 0

    # result rows — same parameterised where clause
    select_sql = (
        "SELECT g.grant_number, g.title, g.title_english, g.responsible_applicant, "  # noqa: S608
        "g.research_institution, g.main_discipline, g.funding_instrument, "
        "g.keywords, g.state, g.start_date, g.end_date, g.amount_granted, "
        "COALESCE(oc.n_publications, 0) AS n_publications, "
        "COALESCE(oc.n_datasets, 0) AS n_datasets, "
        f"COALESCE(oc.n_collaborations, 0) AS n_collaborations FROM grants g {_JOIN} "
        f"WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?"
    )
    cur = conn.execute(select_sql, [*params, limit, offset])
    description = cur.description
    rows = [_row_to_dict(description, row) for row in cur.fetchall()]

    return {"total": total, "results": rows}


# ---------------------------------------------------------------------------
# facet_counts
# ---------------------------------------------------------------------------


def facet_counts(
    store: SnsfStore,
    filters: GrantFilters,
    *,
    text: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return per-facet value->count with excluded-self semantics.

    For each categorical facet, the count query applies all *other* active
    filters but omits the filter for the facet being counted -- matching the
    standard faceted-search "fold" behaviour on the SNSF website.

    Returns
    -------
    {facet_name: [{"value": ..., "count": ...}, ...], ...}
    """
    conn = store.connect()
    result: dict[str, list[dict[str, Any]]] = {}

    # column-backed facets -- col is a fixed string from _COL_FACETS, never user input
    for col in _COL_FACETS:
        where, params = _build_where(filters, text, exclude=col)
        sql = (
            f"SELECT g.{col} AS value, count(*) AS count FROM grants g {_JOIN} "  # noqa: S608
            f"WHERE {where} AND g.{col} IS NOT NULL GROUP BY g.{col} "
            "ORDER BY count DESC, value LIMIT 100"
        )
        cur = conn.execute(sql, params)
        result[col] = [{"value": row[0], "count": row[1]} for row in cur.fetchall()]

    # country facet (via grant_countries)
    where_c, params_c = _build_where(filters, text, exclude="country")
    country_sql = (
        "SELECT gc.country AS value, count(DISTINCT gc.grant_number) AS count "  # noqa: S608
        "FROM grant_countries gc WHERE gc.grant_number IN "
        f"(SELECT g.grant_number FROM grants g {_JOIN} WHERE {where_c}) "
        "GROUP BY gc.country ORDER BY count DESC LIMIT 100"
    )
    cur_c = conn.execute(country_sql, params_c)
    result["country"] = [{"value": row[0], "count": row[1]} for row in cur_c.fetchall()]

    return result


__all__ = ["GrantFilters", "facet_counts", "query_grants"]
