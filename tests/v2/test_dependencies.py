from __future__ import annotations

import asyncio
from dataclasses import fields
from unittest.mock import MagicMock

from fastapi import FastAPI
from starlette.requests import Request

from git_metadata_extractor.agents import ProviderSet
from git_metadata_extractor.dependencies import get_provider_set
from git_metadata_extractor.providers.base import (
    GitHubProvider,
    InfoscienceProvider,
    ORCIDProvider,
    RORProvider,
)
from git_metadata_extractor.providers.epfl_graph_rag import EpflGraphRagProvider
from git_metadata_extractor.providers.ethz_research_collection_rag import (
    EthzResearchCollectionRagProvider,
)
from git_metadata_extractor.providers.federated_rag import FederatedRagProvider
from git_metadata_extractor.providers.github_provider import RealGitHubProvider
from git_metadata_extractor.providers.github_rag import GitHubRagProvider
from git_metadata_extractor.providers.huggingface_rag import HuggingFaceRagProvider
from git_metadata_extractor.providers.infoscience_rag import InfoscienceRagProvider
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider
from git_metadata_extractor.providers.mock_orcid import MockORCIDProvider
from git_metadata_extractor.providers.mock_ror import MockRORProvider
from git_metadata_extractor.providers.oamonitor_rag import OamonitorRagProvider
from git_metadata_extractor.providers.openalex_rag import OpenAlexRagProvider
from git_metadata_extractor.providers.orcid_rag import OrcidRagProvider
from git_metadata_extractor.providers.renkulab_rag import RenkulabRagProvider
from git_metadata_extractor.providers.ror_rag import RorRagProvider
from git_metadata_extractor.providers.snsf_rag import SnsfRagProvider
from git_metadata_extractor.providers.swissubase_rag import SwissubaseRagProvider
from git_metadata_extractor.providers.zenodo_rag import ZenodoRagProvider

# The type `get_provider_set` accepts on `app.state.v2_<field>_provider` for
# each ProviderSet field. `package_registry` is the one field it builds from
# the environment rather than app state, so it has nothing to inject.
_APP_STATE_PROVIDER_TYPES: dict[str, type] = {
    "github": GitHubProvider,
    "orcid": ORCIDProvider,
    "infoscience": InfoscienceProvider,
    "ror": RORProvider,
    "infoscience_rag": InfoscienceRagProvider,
    "ethz_research_collection_rag": EthzResearchCollectionRagProvider,
    "huggingface_rag": HuggingFaceRagProvider,
    "openalex_rag": OpenAlexRagProvider,
    "zenodo_rag": ZenodoRagProvider,
    "oamonitor_rag": OamonitorRagProvider,
    "orcid_rag": OrcidRagProvider,
    "ror_rag": RorRagProvider,
    "snsf_rag": SnsfRagProvider,
    "swissubase_rag": SwissubaseRagProvider,
    "renkulab_rag": RenkulabRagProvider,
    "github_rag": GitHubRagProvider,
    "epfl_graph_rag": EpflGraphRagProvider,
    "federated_rag": FederatedRagProvider,
}


def _build_request(
    *,
    full_path: str = "github.com/octocat/Hello-World",
    query_string: str = "",
) -> Request:
    extract_path = f"/v2/extract/{full_path}"
    app = FastAPI()
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": extract_path,
        "raw_path": extract_path.encode("utf-8"),
        "query_string": query_string.encode("utf-8"),
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": app,
        "path_params": {"full_path": full_path},
    }
    return Request(scope)


def test_get_provider_set_uses_mock_providers_when_flag_unset(monkeypatch) -> None:
    # conftest sets the flag for every test; the documented default is what
    # an unset variable means.
    monkeypatch.delenv("V2_USE_MOCK_PROVIDERS")

    provider_set = asyncio.run(get_provider_set(_build_request()))

    assert isinstance(provider_set.github, MockGitHubProvider)
    assert isinstance(provider_set.orcid, MockORCIDProvider)
    assert isinstance(provider_set.infoscience, MockInfoscienceProvider)
    assert isinstance(provider_set.ror, MockRORProvider)


def test_get_provider_set_returns_every_provider_injected_on_app_state() -> None:
    # Driven by the dataclass, so a field added to ProviderSet without a type
    # above fails here instead of being silently left out of the bundle.
    request = _build_request()
    stand_ins: dict[str, MagicMock] = {}
    for field in fields(ProviderSet):
        if field.name == "package_registry":
            continue
        stand_in = MagicMock(spec=_APP_STATE_PROVIDER_TYPES[field.name])
        setattr(request.app.state, f"v2_{field.name}_provider", stand_in)
        stand_ins[field.name] = stand_in

    provider_set = asyncio.run(get_provider_set(request))

    dropped = [
        name for name, stand_in in stand_ins.items() if getattr(provider_set, name) is not stand_in
    ]
    assert dropped == []


def test_get_provider_set_disables_github_repo_expansion_for_repository_extract(
    monkeypatch,
) -> None:
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "false")


    provider_set = asyncio.run(
        get_provider_set(_build_request(full_path="github.com/octocat/Hello-World")),
    )

    assert isinstance(provider_set.github, RealGitHubProvider)
    assert provider_set.github.include_user_repositories is False
    assert provider_set.github.include_organization_repositories is False


def test_get_provider_set_keeps_github_repo_expansion_for_user_extract(
    monkeypatch,
) -> None:
    monkeypatch.setenv("V2_USE_MOCK_PROVIDERS", "false")


    provider_set = asyncio.run(
        get_provider_set(_build_request(full_path="github.com/octocat")),
    )

    assert isinstance(provider_set.github, RealGitHubProvider)
    assert provider_set.github.include_user_repositories is True
    assert provider_set.github.include_organization_repositories is True
