from __future__ import annotations

from typing import Any

from src.v2.agents.llm.agent_tools.organization_identity import (
    make_organization_identity_search_tool,
)


class _FakeRORProvider:
    def search_organizations(self, query: str) -> list[dict[str, Any]]:
        normalized = query.strip().lower()
        if normalized in {"sdsc-ordes", "swiss data science center"}:
            return [
                {
                    "id": "https://ror.org/02hdt9m26",
                    "name": "Swiss Data Science Center",
                    "aliases": [],
                    "acronyms": ["SDSC"],
                },
            ]
        return []


class _FakeInfoscienceProvider:
    def search_orgunit(self, query: str) -> list[dict[str, Any]]:
        normalized = query.strip().lower()
        if normalized in {"swiss data science center", "swiss data science centre"}:
            return [
                {
                    "infoscienceOrgUnitIdentifier": "0469064e-5977-4569-93a2-522b6d758e50",
                    "name": "Swiss Data Science Centre",
                    "acronym": "SDSC",
                    "parentOrganization": "EPFL",
                },
            ]
        return []


def test_search_organization_identity_links_center_and_centre_variants() -> None:
    tool = make_organization_identity_search_tool(
        _FakeRORProvider(),
        _FakeInfoscienceProvider(),
    )
    payload = tool.function("sdsc-ordes")

    assert payload["query"] == "sdsc-ordes"
    assert payload["ror_candidates"]
    assert payload["infoscience_candidates"]
    assert payload["linked_candidates"]
    linked = payload["linked_candidates"][0]
    assert linked["ror_id"] == "https://ror.org/02hdt9m26"
    assert linked["infoscience_orgunit_identifier"] == "0469064e-5977-4569-93a2-522b6d758e50"
    assert linked["normalized_name"] == "swiss data science center"
    assert "Swiss Data Science Center" in payload["expanded_queries"]["infoscience"]


def test_search_organization_identity_handles_empty_query() -> None:
    tool = make_organization_identity_search_tool(
        _FakeRORProvider(),
        _FakeInfoscienceProvider(),
    )
    payload = tool.function("   ")

    assert payload["query"] == ""
    assert payload["ror_candidates"] == []
    assert payload["infoscience_candidates"] == []
    assert payload["linked_candidates"] == []
    assert payload["expanded_queries"] == {"ror": [], "infoscience": []}

