"""@handle mentions in `_company` / `_bio` are linked as GitHub orgs.

A person's free-text `_company` ("@google-deepmind") or `_bio` ("now at
@huggingface") often names the affiliation by GitHub handle. A handle GitHub
confirms is an *organization* is linked directly as a github-org affiliation
(which then resolves to ROR via the web-domain cascade), instead of being
fed to the weaker free-text ROR search.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.v2.pipeline.stages.models import ReconciledEntities
from src.v2.pipeline.stages.resolve_company_to_ror import (
    run_resolve_company_to_ror_stage,
)


class _StubGitHub:
    def __init__(self, lookups: dict[str, dict[str, Any]]) -> None:
        self._lookups = lookups
        self.calls: list[str] = []

    def get_user(self, login: str) -> dict[str, Any]:
        self.calls.append(login)
        return self._lookups.get(login.lower(), {})


class _NoRor:
    async def search(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return []


def _run(person: dict[str, Any], github: Any) -> ReconciledEntities:
    rec = ReconciledEntities(
        entities={"persons": [person], "organizations": [], "memberships": []},
    )
    asyncio.run(
        run_resolve_company_to_ror_stage(
            reconciled=rec, provider=_NoRor(), github_provider=github,
        ),
    )
    return rec


def test_company_and_bio_at_handles_become_github_orgs() -> None:
    gh = _StubGitHub(
        {
            "google-deepmind": {"type": "Organization", "name": "Google DeepMind"},
            "huggingface": {"type": "Organization", "name": "Hugging Face"},
            "someuser": {"type": "User"},
        },
    )
    person = {
        "id": "https://github.com/alice",
        "schema:name": "Alice",
        "_company": "@google-deepmind",
        "_bio": "PhD; now at @huggingface and @someuser",
    }
    rec = _run(person, gh)

    orgs = {o["id"]: o for o in rec.entities["organizations"]}
    assert set(orgs) == {
        "https://github.com/google-deepmind",
        "https://github.com/huggingface",
    }
    gd = orgs["https://github.com/google-deepmind"]
    assert gd["idSource"] == "pulse:githubOrganizationHandle"
    assert gd["pulse:githubOrganizationHandle"] == "https://github.com/google-deepmind"
    assert gd["schema:name"] == "Google DeepMind"
    # @someuser is a User, not an org -> no entity for it.
    assert "https://github.com/someuser" not in orgs
    member_orgs = {m["org:organization"] for m in rec.entities["memberships"]}
    assert member_orgs == set(orgs)


def test_non_org_handle_is_not_linked() -> None:
    gh = _StubGitHub({"someuser": {"type": "User"}})
    rec = _run({"id": "p", "_company": "@someuser"}, gh)
    assert rec.entities["organizations"] == []
    assert rec.entities["memberships"] == []


def test_idempotent_across_reruns() -> None:
    gh = _StubGitHub({"google-deepmind": {"type": "Organization", "name": "Google DeepMind"}})
    person = {"id": "https://github.com/alice", "_company": "@google-deepmind"}
    rec = ReconciledEntities(
        entities={"persons": [person], "organizations": [], "memberships": []},
    )
    for _ in range(2):
        asyncio.run(
            run_resolve_company_to_ror_stage(
                reconciled=rec, provider=_NoRor(), github_provider=gh,
            ),
        )
    assert len(rec.entities["organizations"]) == 1
    assert len(rec.entities["memberships"]) == 1


def test_no_github_provider_is_a_noop_for_handles() -> None:
    # Without a github provider, the @handle falls through to ROR (here: none).
    rec = _run({"id": "p", "_company": "@google-deepmind"}, None)
    assert rec.entities["organizations"] == []
