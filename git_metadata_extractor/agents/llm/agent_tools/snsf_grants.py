"""Pydantic-AI Tool backed by :class:`SnsfGrantsProvider`.

``search_snsf_grants`` — faceted + free-text search over ~90 k Swiss National
Science Foundation grants in the SNSF P3 grants database.

Search returns thin hits (grant_number URL, title, applicant, institution,
scheme, discipline, state, dates, amount, output counts), which keeps prompts
small.
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
    "amount_granted, n_publications, n_datasets, n_collaborations)."
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


__all__ = [
    "make_search_snsf_grants_tool",
]
