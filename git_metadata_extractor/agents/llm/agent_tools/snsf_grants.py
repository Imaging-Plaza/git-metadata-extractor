"""Pydantic-AI Tools backed by :class:`SnsfGrantsProvider`.

Three tools so the LLM can drive a search/facets/fetch loop over the SNSF
P3 grants database:

* ``search_snsf_grants`` — faceted + free-text search over ~90 k Swiss
  National Science Foundation grants (thin rows).
* ``snsf_grant_facets`` — per-facet value→count so the agent can discover
  available filter values.
* ``fetch_snsf_grant`` — full grant record (incl. abstract / lay summaries)
  by grant number or URL.

Splitting search from fetch keeps prompts small: search returns thin hits
(grant_number URL, title, applicant, institution, scheme, discipline, state,
dates, amount, output counts) and the agent decides when to spend the context
budget on a full body.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.snsf_grants import SnsfGrantsProvider

logger = logging.getLogger(__name__)

_SEARCH_DESCRIPTION = (
    "Faceted + free-text search over the SNSF P3 grants database (~90 k "
    "Swiss National Science Foundation grants). Use this when README / "
    "CITATION / metadata mentions an SNSF grant, a Swiss-funded project, "
    "or a PI at a Swiss institution, or to enrich an entity with its SNSF "
    "grants. "
    "Filter params: `funding_instrument` (e.g. 'ProjectFunding', 'Ambizione', "
    "'NCCR'), `research_institution` (e.g. 'EPF Lausanne - EPFL', 'ETH Zurich'), "
    "`state` (e.g. 'Active', 'Completed'), `main_discipline`, "
    "`main_field_of_research`, `country` (collaboration country), "
    "`person_number` (SNSF person id int), `has_output` (list of output types "
    "e.g. ['publications', 'datasets']), `start_from`/`start_to` (ISO date "
    "strings, e.g. '2020-01-01'). All list filters are OR within the list. "
    "`text` is a free-text ILIKE match across title/abstract/keywords. "
    "`sort`: 'start_date_desc' (default), 'start_date_asc', 'amount_desc', "
    "'amount_asc'. `limit`: default 20. "
    "Returns thin rows (grant_number URL, title, title_english, "
    "responsible_applicant, research_institution, main_discipline, "
    "funding_instrument, keywords, state, start_date, end_date, "
    "amount_granted, n_publications, n_datasets, n_collaborations). "
    "Call fetch_snsf_grant with a grant_number to get the full record "
    "including abstract and lay summaries."
)

_FACETS_DESCRIPTION = (
    "Return per-facet value→count for the SNSF grants database given the "
    "current filter set (excluded-self semantics: each facet's counts are "
    "computed without that facet's own active filter). Use this to discover "
    "available filter values before or alongside search_snsf_grants. "
    "Accepts the same filter params as search_snsf_grants."
)

_FETCH_DESCRIPTION = (
    "Fetch the full SNSF grant record (including abstract and lay summaries "
    "in EN/DE/FR/IT) by grant_number (canonical URL like "
    "'https://data.snf.ch/grants/grant/12345' or bare integer string). "
    "Use only after a promising search hit — it spends meaningful context "
    "budget. Returns the complete grants table row."
)


def _service_name(op: str) -> str:
    return f"snsf_grants.{op}"


def make_search_snsf_grants_tool(provider: SnsfGrantsProvider) -> Tool:
    """Tool factory: faceted + free-text search over SNSF grants."""

    async def search_snsf_grants(  # noqa: PLR0913
        funding_instrument: list[str] | None = None,
        research_institution: list[str] | None = None,
        state: list[str] | None = None,
        main_discipline: list[str] | None = None,
        main_field_of_research: list[str] | None = None,
        country: list[str] | None = None,
        person_number: int | None = None,
        has_output: list[str] | None = None,
        start_from: str | None = None,
        start_to: str | None = None,
        text: str | None = None,
        sort: str = "start_date_desc",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Faceted + free-text search over SNSF P3 grants. See tool description."""
        from open_pulse_sources.index.snsf.facet_query import GrantFilters  # noqa: PLC0415

        logger.info(
            "tool call: search_snsf_grants — state=%r institution=%r text=%r limit=%d",
            state, research_institution, text, limit,
        )
        record_query(
            service=_service_name("search"),
            query=text or str(state or ""),
        )
        filters = GrantFilters(
            funding_instrument=funding_instrument,
            research_institution=research_institution,
            state=state,
            main_discipline=main_discipline,
            main_field_of_research=main_field_of_research,
            country=country,
            person_number=person_number,
            has_output=has_output,
            start_from=start_from,
            start_to=start_to,
        )
        return provider.search(filters, text=text, sort=sort, limit=limit)

    return Tool(
        search_snsf_grants,
        name="search_snsf_grants",
        description=_SEARCH_DESCRIPTION,
    )


def make_snsf_grant_facets_tool(provider: SnsfGrantsProvider) -> Tool:
    """Tool factory: facet counts for a filter set."""

    async def snsf_grant_facets(  # noqa: PLR0913
        funding_instrument: list[str] | None = None,
        research_institution: list[str] | None = None,
        state: list[str] | None = None,
        main_discipline: list[str] | None = None,
        main_field_of_research: list[str] | None = None,
        country: list[str] | None = None,
        person_number: int | None = None,
        has_output: list[str] | None = None,
        start_from: str | None = None,
        start_to: str | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        """Return facet counts for the SNSF grants database. See tool description."""
        from open_pulse_sources.index.snsf.facet_query import GrantFilters  # noqa: PLC0415

        logger.info("tool call: snsf_grant_facets — state=%r text=%r", state, text)
        record_query(
            service=_service_name("facets"),
            query=text or str(state or ""),
        )
        filters = GrantFilters(
            funding_instrument=funding_instrument,
            research_institution=research_institution,
            state=state,
            main_discipline=main_discipline,
            main_field_of_research=main_field_of_research,
            country=country,
            person_number=person_number,
            has_output=has_output,
            start_from=start_from,
            start_to=start_to,
        )
        return provider.facets(filters, text=text)

    return Tool(
        snsf_grant_facets,
        name="snsf_grant_facets",
        description=_FACETS_DESCRIPTION,
    )


def make_fetch_snsf_grant_tool(provider: SnsfGrantsProvider) -> Tool:
    """Tool factory: fetch the full grant record by grant_number."""

    async def fetch_snsf_grant(
        grant_number: str,
    ) -> dict[str, Any] | None:
        """Fetch full SNSF grant record incl. abstract. See tool description."""
        logger.info("tool call: fetch_snsf_grant — grant_number=%s", grant_number)
        record_query(
            service=_service_name("fetch"),
            query=grant_number,
        )
        return provider.fetch(grant_number)

    return Tool(
        fetch_snsf_grant,
        name="fetch_snsf_grant",
        description=_FETCH_DESCRIPTION,
    )


__all__ = [
    "make_fetch_snsf_grant_tool",
    "make_search_snsf_grants_tool",
    "make_snsf_grant_facets_tool",
]
