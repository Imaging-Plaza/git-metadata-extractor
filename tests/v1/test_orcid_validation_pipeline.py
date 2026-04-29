"""
Regression tests for ORCID sanitization in repository analysis and JSON-LD conversion.
"""

from datetime import datetime
import asyncio

from fastapi import Response

import src.api as api_module
import src.v1.analysis.repositories as repositories_module
import src.v1.cache.cache_manager as cache_manager_module
from src.v1.analysis.repositories import Repository
from src.v1.data_models.conversion import (
    convert_pydantic_to_jsonld,
    create_simplified_model,
)
from src.v1.data_models.models import Person
from src.v1.data_models.repository import SoftwareSourceCode
from src.v1.utils.url_validation import (
    normalize_orcid_id,
    normalize_orcid_url,
    validate_author_urls,
)


def _build_repository(tmp_path, monkeypatch) -> Repository:
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setattr(
        repositories_module,
        "is_github_repo_public",
        lambda _repo_url: True,
    )
    cache_manager_module._cache_manager = None
    repo = Repository("https://github.com/example/repo", force_refresh=False)
    repo.gimie = None
    return repo


def test_union_reconciliation_drops_invalid_orcid(tmp_path, monkeypatch):
    repo = _build_repository(tmp_path, monkeypatch)
    _, union_metadata = create_simplified_model(
        SoftwareSourceCode,
        field_filter=["author"],
    )

    simplified_dict = {
        "authorPerson": [
            {
                "name": "Bad ORCID Author",
                "orcid": "0000-0009-0008-0143-9118",
                "emails": ["bad.orcid@example.org"],
                "affiliations": [],
            },
        ],
        "authorOrganization": None,
    }

    full_dict = repo._convert_simplified_to_full(
        simplified_dict=simplified_dict,
        union_metadata=union_metadata,
        git_authors=[],
    )

    assert "author" in full_dict
    assert len(full_dict["author"]) == 1
    author = full_dict["author"][0]
    assert isinstance(author, Person)
    assert author.name == "Bad ORCID Author"
    assert author.orcid is None


def test_orcid_normalization_and_jsonld_conversion_for_valid_values():
    valid_id = "0000-0002-1126-1535"
    valid_url = "https://orcid.org/0000-0002-1126-1535"

    assert normalize_orcid_id(valid_id) == valid_id
    assert normalize_orcid_id(valid_url) == valid_id
    assert normalize_orcid_url(valid_id) == valid_url

    person = Person(
        id="https://github.com/tester",
        name="Tester",
        orcid=valid_url,
    )
    jsonld = convert_pydantic_to_jsonld(person)
    graph = jsonld.get("@graph", [])
    assert any(
        node.get("md4i:orcidId", {}).get("@id") == valid_url
        for node in graph
        if isinstance(node, dict)
    )


def test_merge_person_objects_prefers_valid_orcid_when_mixed(tmp_path, monkeypatch):
    repo = _build_repository(tmp_path, monkeypatch)

    valid_person = Person(
        id="https://github.com/valid",
        name="Robin Franken",
        orcid="0000-0002-6441-8540",
    )
    invalid_person = Person.model_construct(
        id="https://github.com/invalid",
        type="Person",
        name="Robin Franken",
        emails=[],
        githubId="rmfranken",
        orcid="0000-0009-0008-0143-9118",
        affiliations=[],
        affiliationHistory=[],
        source="gimie",
        linkedEntities=[],
    )

    merged = repo._merge_person_objects([valid_person, invalid_person])
    assert merged.orcid == "0000-0002-6441-8540"


def test_merge_person_objects_drops_only_invalid_orcid_values(tmp_path, monkeypatch):
    repo = _build_repository(tmp_path, monkeypatch)

    invalid_one = Person.model_construct(
        id="https://github.com/a",
        type="Person",
        name="Same Author",
        emails=[],
        githubId=None,
        orcid="0000-0009-0008-0143-9118",
        affiliations=[],
        affiliationHistory=[],
        source="gimie",
        linkedEntities=[],
    )
    invalid_two = Person.model_construct(
        id="https://github.com/b",
        type="Person",
        name="Same Author",
        emails=[],
        githubId=None,
        orcid="0000-0009-0008-0143-9118",
        affiliations=[],
        affiliationHistory=[],
        source="gimie",
        linkedEntities=[],
    )

    merged = repo._merge_person_objects([invalid_one, invalid_two])
    assert merged.orcid is None


def test_validate_author_urls_normalizes_orcid_and_drops_invalid():
    valid_author = validate_author_urls(
        {"name": "Author A", "orcid": "https://orcid.org/0000-0002-1126-1535"},
    )
    invalid_author = validate_author_urls(
        {"name": "Author B", "orcid": "0000-0009-0008-0143-9118"},
    )

    assert valid_author["orcid"] == "0000-0002-1126-1535"
    assert invalid_author["orcid"] is None


def test_llm_jsonld_endpoint_handles_malformed_orcid_without_500(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "cache.db"))

    class FakeRepository(repositories_module.Repository):
        def __init__(self, full_path: str, force_refresh: bool = False):
            self.full_path = full_path
            self.force_refresh = force_refresh
            self.data = None
            self.gimie = None

            # Stats fields consumed by get_usage_stats() in the API endpoint.
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.estimated_input_tokens = 0
            self.estimated_output_tokens = 0
            self.start_time = datetime.now()
            self.end_time = self.start_time
            self.analysis_successful = True

        async def run_analysis(
            self,
            run_gimie: bool = True,
            run_llm: bool = True,
            run_user_enrichment: bool = False,
            run_organization_enrichment: bool = False,
        ):
            # Build output through the real reconciliation path using malformed ORCID.
            _, union_metadata = create_simplified_model(
                SoftwareSourceCode,
                field_filter=["author"],
            )
            simplified_dict = {
                "name": "orcid-regression-test",
                "description": "Regression fixture",
                "repositoryType": "software",
                "repositoryTypeJustification": ["test fixture"],
                "authorPerson": [
                    {
                        "name": "Malformed ORCID Author",
                        "orcid": "0000-0009-0008-0143-9118",
                        "emails": ["bad.orcid@example.org"],
                        "affiliations": [],
                    },
                ],
                "authorOrganization": None,
            }
            full_dict = self._convert_simplified_to_full(
                simplified_dict=simplified_dict,
                union_metadata=union_metadata,
                git_authors=[],
            )
            self.data = SoftwareSourceCode.model_validate(full_dict)

        def dump_results(self, output_type="json"):
            return self.data.convert_pydantic_to_jsonld()

        def get_usage_stats(self):
            return {
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "total_tokens": self.total_input_tokens + self.total_output_tokens,
                "estimated_input_tokens": self.estimated_input_tokens,
                "estimated_output_tokens": self.estimated_output_tokens,
                "estimated_total_tokens": self.estimated_input_tokens
                + self.estimated_output_tokens,
                "duration": 0.0,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "status_code": 200,
            }

    def fake_validate_github_token():
        now = datetime.now()
        return {
            "valid": True,
            "rate_limit_limit": 5000,
            "rate_limit_remaining": 4999,
            "rate_limit_reset": now,
        }

    monkeypatch.setattr(api_module, "Repository", FakeRepository)
    response = Response()
    result = asyncio.run(
        api_module.llm_jsonld(
            response=response,
            full_path="https://github.com/example/repo",
            force_refresh=True,
            enrich_orgs=False,
            enrich_users=False,
            github_info=fake_validate_github_token(),
        ),
    )

    assert result.output is not None
    assert "@graph" in result.output

    graph_nodes = result.output["@graph"]
    malformed_orcid = "https://orcid.org/0000-0009-0008-0143-9118"
    assert not any(
        node.get("md4i:orcidId", {}).get("@id") == malformed_orcid
        for node in graph_nodes
        if isinstance(node, dict)
    )
