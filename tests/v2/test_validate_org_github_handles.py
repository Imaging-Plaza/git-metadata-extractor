from __future__ import annotations

from typing import Any

from src.v2.agents.models import ProviderSet
from src.v2.pipeline.stages.models import ReconciledEntities
from src.v2.pipeline.stages.validate_org_github_handles import (
    validate_org_github_handles,
)


class _StubGitHubProvider:
    def __init__(self, lookups: dict[str, dict[str, Any]]) -> None:
        self._lookups = lookups
        self.calls: list[str] = []

    def get_user(self, login: str) -> dict[str, Any]:
        self.calls.append(login)
        return self._lookups.get(login, {})


def _org_with_at_name(name: str) -> dict[str, Any]:
    return {
        "id": f"urn:pulse:test-{name.lstrip('@')}",
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "schema:name": name,
        "identifiers": {
            "pulse:ror": None,
            "pulse:githubOrganizationHandle": None,
            "pulse:infoscienceOrganizationIdentifier": None,
            "uuid": "00000000-0000-0000-0000-000000000000",
        },
    }


def test_stamps_handle_when_github_says_organization() -> None:
    provider = _StubGitHubProvider(
        lookups={"10xGenomics": {"login": "10xGenomics", "type": "Organization"}},
    )
    org = _org_with_at_name("@10xGenomics")
    reconciled = ReconciledEntities(entities={"organizations": [org]})

    result, warnings = validate_org_github_handles(
        reconciled,
        ProviderSet(github=provider),
    )

    orgs = result.entities["organizations"]
    assert len(orgs) == 1
    # v3.0.0: stamped in canonical URL form so it passes the strict
    # `^https://github\.com/…` pattern (bare handle got the org excluded).
    assert orgs[0]["pulse:githubOrganizationHandle"] == "https://github.com/10xGenomics"
    assert (
        orgs[0]["identifiers"]["pulse:githubOrganizationHandle"]
        == "https://github.com/10xGenomics"
    )
    assert orgs[0]["schema:name"] == "10xGenomics"  # leading @ stripped
    assert any("Stamped pulse:githubOrganizationHandle='10xGenomics'" in w for w in warnings)


def test_drops_when_github_says_user() -> None:
    provider = _StubGitHubProvider(
        lookups={"someone": {"login": "someone", "type": "User"}},
    )
    org = _org_with_at_name("@someone")
    reconciled = ReconciledEntities(entities={"organizations": [org]})

    result, warnings = validate_org_github_handles(
        reconciled,
        ProviderSet(github=provider),
    )

    assert result.entities["organizations"] == []
    assert any("Dropped hallucinated organization '@someone'" in w for w in warnings)


def test_drops_when_github_returns_404() -> None:
    provider = _StubGitHubProvider(lookups={})  # any lookup returns {}
    org = _org_with_at_name("@nonexistent")
    reconciled = ReconciledEntities(entities={"organizations": [org]})

    result, _warnings = validate_org_github_handles(
        reconciled,
        ProviderSet(github=provider),
    )

    assert result.entities["organizations"] == []


def test_skips_orgs_already_carrying_a_handle() -> None:
    provider = _StubGitHubProvider(lookups={})
    org = _org_with_at_name("@10xGenomics")
    org["pulse:githubOrganizationHandle"] = "10xGenomics"
    reconciled = ReconciledEntities(entities={"organizations": [org]})

    result, warnings = validate_org_github_handles(
        reconciled,
        ProviderSet(github=provider),
    )

    assert result.entities["organizations"] == [org]
    assert provider.calls == []  # never probed
    assert warnings == []


def test_skips_when_name_is_not_at_handle_shaped() -> None:
    provider = _StubGitHubProvider(lookups={})
    org = _org_with_at_name("Acme Research Group")  # not an @-mention
    reconciled = ReconciledEntities(entities={"organizations": [org]})

    result, warnings = validate_org_github_handles(
        reconciled,
        ProviderSet(github=provider),
    )

    assert result.entities["organizations"] == [org]
    assert provider.calls == []
    assert warnings == []


def test_provider_lookup_failure_keeps_entity() -> None:
    class _Fails(_StubGitHubProvider):
        def get_user(self, login: str) -> dict[str, Any]:
            raise RuntimeError("boom")

    provider = _Fails(lookups={})
    org = _org_with_at_name("@10xGenomics")
    reconciled = ReconciledEntities(entities={"organizations": [org]})

    result, warnings = validate_org_github_handles(
        reconciled,
        ProviderSet(github=provider),
    )

    assert result.entities["organizations"] == [org]
    assert warnings == []  # informational only via logger
