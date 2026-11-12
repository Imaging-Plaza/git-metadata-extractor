"""HTTP-only SNSF faceted grant retrieval."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from git_metadata_extractor.providers._remote_retrieval import RemoteRetrievalProvider


class GrantFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    funding_instrument: list[str] | None = None
    research_institution: list[str] | None = None
    state: list[str] | None = None
    main_discipline: list[str] | None = None
    main_field_of_research: list[str] | None = None
    call_decision_year: list[int] | None = None
    country: list[str] | None = None
    person_number: int | None = None
    person_role: str | None = None
    has_output: list[str] | None = None
    start_from: str | None = None
    start_to: str | None = None
    end_from: str | None = None
    end_to: str | None = None


class SnsfGrantsProvider(RemoteRetrievalProvider):
    provider_name = "snsf_grants"

    async def search(
        self,
        filters: GrantFilters,
        *,
        text: str | None = None,
        sort: str = "start_date_desc",
        limit: int = 20,
    ) -> list[dict]:
        return await self._call(
            "search",
            {
                "filters": filters.model_dump(exclude_none=True),
                "text": text,
                "sort": sort,
                "limit": limit,
            },
            empty=[],
        )
