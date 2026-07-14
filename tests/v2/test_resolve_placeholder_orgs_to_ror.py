"""Tests for `resolve_placeholder_orgs_to_ror` — the late pass that
rewrites `idSource=uuid` Organization placeholders into ROR-anchored
Orgs and patches their referring Memberships.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from git_metadata_extractor.pipeline.stages.models import ReconciledEntities
from git_metadata_extractor.pipeline.stages.resolve_placeholder_orgs_to_ror import (
    STAGE_SOURCE_TAG,
    PlaceholderResolutionResult,
    run_resolve_placeholder_orgs_to_ror_stage,
)


class _StubProvider:
    """Cleaned query → list of ROR hit dicts. Mirrors the company-stage
    test stub so we exercise the same `_resolve_one` code path."""

    def __init__(self, hits: dict[str, list[dict[str, Any]]]) -> None:
        self._hits = hits
        self.queries: list[str] = []

    async def search(
        self,
        *,
        query: str,
        scope_mode: str = "worldwide",
        top_k: int = 5,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        self.queries.append(query)
        return list(self._hits.get(query, []))


def _placeholder_org(uuid: str, name: str) -> dict[str, Any]:
    """Build a placeholder Org as the rescue / fallback paths would
    leave it — `idSource = "uuid"`, name is the original affiliation
    string carried as a breadcrumb."""
    return {
        "id": uuid,
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "identifiers": {"pulse:ror": None, "uuid": uuid},
        "idSource": "uuid",
        "schema:name": name,
    }


def _membership(person_id: str, org_id: str) -> dict[str, Any]:
    composite = f"{person_id}__{org_id}"
    return {
        "id": composite,
        "type": "org:Membership",
        "shacl": "pulse:MembershipShape",
        "identifiers": {"pulse:composite": composite, "uuid": "fixed-uuid"},
        "idSource": "pulse:composite",
        "org:organization": org_id,
        "org:role": None,
        "time:hasBeginning": None,
        "time:hasEnd": None,
    }


def _run(reconciled: ReconciledEntities, provider: Any) -> PlaceholderResolutionResult:
    return asyncio.run(
        run_resolve_placeholder_orgs_to_ror_stage(
            reconciled=reconciled, provider=provider,
        ),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_placeholder_with_resolvable_name_gets_rewritten_to_ror():
    """A `idSource=uuid` Org with `schema:name='EPFL'` resolves to the
    EPFL ROR, the Org is rewritten in place, and the Membership that
    referenced its uuid gets its `org:organization` + composite id
    patched."""
    placeholder = _placeholder_org("abc-uuid", "EPFL")
    membership = _membership("alice", "abc-uuid")
    reconciled = ReconciledEntities(
        entities={
            "persons": [{"id": "alice", "schema:name": "Alice"}],
            "organizations": [placeholder],
            "memberships": [membership],
        },
    )
    provider = _StubProvider(
        hits={
            "EPFL": [
                {"score": 0.95, "types": ["education"], "name": "EPFL", "ror_id": "https://ror.org/02s376052"},
                {"score": 0.40, "types": ["education"], "name": "Other"},
            ],
        },
    )

    result = _run(reconciled, provider)

    org = reconciled.entities["organizations"][0]
    assert org["id"] == "https://ror.org/02s376052"
    assert org["idSource"] == "pulse:ror"
    assert org["identifiers"]["pulse:ror"] == "https://ror.org/02s376052"
    # uuid identifier dropped — no longer canonical.
    assert "uuid" not in org["identifiers"]
    assert org["_source"] == STAGE_SOURCE_TAG
    # schema:name canonicalized to ROR display name; original kept under _original_name.
    assert org["schema:name"] == "EPFL"

    m = reconciled.entities["memberships"][0]
    assert m["id"] == "alice__https://ror.org/02s376052"
    assert m["org:organization"] == "https://ror.org/02s376052"
    assert m["identifiers"]["pulse:composite"] == "alice__https://ror.org/02s376052"
    assert m["_source"] == STAGE_SOURCE_TAG

    assert result.placeholders_examined == 1
    assert result.placeholders_resolved == 1
    assert result.memberships_rewritten == 1


def test_canonical_ror_name_replaces_placeholder_and_keeps_original_breadcrumb():
    """If ROR's display name differs from the placeholder breadcrumb,
    the canonical name wins and the original is preserved under
    `_original_name` so the breadcrumb is recoverable with
    `include_internal_fields=true`."""
    placeholder = _placeholder_org("u1", "swiss data science center")
    reconciled = ReconciledEntities(
        entities={"organizations": [placeholder], "memberships": []},
    )
    provider = _StubProvider(
        hits={
            "swiss data science center": [
                {"score": 0.95, "types": ["facility"], "name": "Swiss Data Science Center", "ror_id": "https://ror.org/02hdt9m26"},
            ],
        },
    )

    _run(reconciled, provider)

    org = reconciled.entities["organizations"][0]
    assert org["schema:name"] == "Swiss Data Science Center"
    assert org["_original_name"] == "swiss data science center"


# ---------------------------------------------------------------------------
# Negative paths
# ---------------------------------------------------------------------------


def test_placeholder_with_unresolvable_name_is_left_unchanged():
    placeholder = _placeholder_org("u1", "Some Vague String")
    membership = _membership("alice", "u1")
    reconciled = ReconciledEntities(
        entities={"organizations": [placeholder], "memberships": [membership]},
    )
    # No ROR hit → top hit score 0.4 (below the 0.55 floor).
    provider = _StubProvider(
        hits={
            "Some Vague String": [
                {"score": 0.40, "types": ["company"], "name": "Other"},
            ],
        },
    )

    result = _run(reconciled, provider)

    org = reconciled.entities["organizations"][0]
    assert org["id"] == "u1"
    assert org["idSource"] == "uuid"
    assert "_source" not in org
    # Membership untouched.
    m = reconciled.entities["memberships"][0]
    assert m["id"] == "alice__u1"
    assert m["org:organization"] == "u1"
    assert "_source" not in m

    assert result.placeholders_resolved == 0
    assert result.memberships_rewritten == 0
    # Rejection reason recorded so an operator can see why.
    assert result.rejection_reasons


def test_org_with_idsource_pulse_ror_is_skipped():
    """An Organization already anchored on ROR isn't a placeholder —
    don't re-query it even if the ROR id happens to look unusual."""
    anchored = {
        "id": "https://ror.org/02s376052",
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "identifiers": {"pulse:ror": "https://ror.org/02s376052", "uuid": "x"},
        "idSource": "pulse:ror",
        "schema:name": "EPFL",
    }
    reconciled = ReconciledEntities(
        entities={"organizations": [anchored], "memberships": []},
    )
    provider = _StubProvider(hits={"EPFL": [{"score": 0.95, "types": ["education"], "name": "EPFL", "ror_id": "X"}]})

    result = _run(reconciled, provider)
    assert result.placeholders_examined == 0
    # No queries issued — the org never made it into the candidate set.
    assert provider.queries == []


def test_placeholder_without_schema_name_is_skipped():
    """A placeholder Org with no name carries no breadcrumb to query.
    Stage skips it rather than emitting an empty-string query."""
    no_name_placeholder = {
        "id": "u1",
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "identifiers": {"uuid": "u1"},
        "idSource": "uuid",
        # schema:name omitted on purpose.
    }
    reconciled = ReconciledEntities(
        entities={"organizations": [no_name_placeholder], "memberships": []},
    )
    provider = _StubProvider(hits={})

    result = _run(reconciled, provider)
    assert result.placeholders_examined == 0
    assert provider.queries == []


# ---------------------------------------------------------------------------
# Cross-membership patching
# ---------------------------------------------------------------------------


def test_multiple_memberships_to_same_placeholder_all_get_patched():
    """If two Persons share a Membership to the same placeholder Org,
    both Memberships get patched in one pass."""
    placeholder = _placeholder_org("u1", "EPFL")
    m_alice = _membership("alice", "u1")
    m_bob = _membership("bob", "u1")
    reconciled = ReconciledEntities(
        entities={
            "persons": [{"id": "alice"}, {"id": "bob"}],
            "organizations": [placeholder],
            "memberships": [m_alice, m_bob],
        },
    )
    provider = _StubProvider(
        hits={
            "EPFL": [
                {"score": 0.95, "types": ["education"], "name": "EPFL", "ror_id": "https://ror.org/02s376052"},
                {"score": 0.40, "types": ["education"], "name": "Other"},
            ],
        },
    )

    result = _run(reconciled, provider)
    assert result.memberships_rewritten == 2
    assert all(
        m["org:organization"] == "https://ror.org/02s376052"
        for m in reconciled.entities["memberships"]
    )


def test_unrelated_memberships_are_not_touched():
    """A Membership to an Org we didn't rewrite (because it resolved
    to nothing) keeps its original `org:organization` value."""
    resolvable = _placeholder_org("u1", "EPFL")
    unresolvable = _placeholder_org("u2", "Local Studio")
    m_keep = _membership("alice", "u2")  # to the unresolvable Org
    m_rewrite = _membership("bob", "u1")  # to the resolvable Org
    reconciled = ReconciledEntities(
        entities={
            "organizations": [resolvable, unresolvable],
            "memberships": [m_keep, m_rewrite],
        },
    )
    provider = _StubProvider(
        hits={
            "EPFL": [
                {"score": 0.95, "types": ["education"], "name": "EPFL", "ror_id": "https://ror.org/02s376052"},
                {"score": 0.40, "types": ["education"], "name": "Other"},
            ],
            # No accepted hit for "Local Studio".
            "Local Studio": [{"score": 0.30, "types": ["company"], "name": "Local"}],
        },
    )

    result = _run(reconciled, provider)
    # Only one membership rewritten.
    assert result.memberships_rewritten == 1
    by_id = {m["id"]: m for m in reconciled.entities["memberships"]}
    assert "alice__u2" in by_id  # untouched (placeholder)
    assert "bob__https://ror.org/02s376052" in by_id  # rewritten


# ---------------------------------------------------------------------------
# Idempotency + edge cases
# ---------------------------------------------------------------------------


def test_stage_is_idempotent_on_re_run():
    """Re-running on a graph the stage already processed is a no-op:
    Orgs now have idSource=pulse:ror so they're not candidates."""
    placeholder = _placeholder_org("u1", "EPFL")
    membership = _membership("alice", "u1")
    reconciled = ReconciledEntities(
        entities={"organizations": [placeholder], "memberships": [membership]},
    )
    provider = _StubProvider(
        hits={
            "EPFL": [
                {"score": 0.95, "types": ["education"], "name": "EPFL", "ror_id": "https://ror.org/02s376052"},
                {"score": 0.40, "types": ["education"], "name": "Other"},
            ],
        },
    )

    first = _run(reconciled, provider)
    second = _run(reconciled, provider)

    assert first.placeholders_resolved == 1
    assert second.placeholders_examined == 0
    assert second.placeholders_resolved == 0
    assert second.memberships_rewritten == 0


def test_stage_returns_zero_when_no_organizations():
    reconciled = ReconciledEntities(entities={"persons": [], "organizations": [], "memberships": []})
    result = asyncio.run(
        run_resolve_placeholder_orgs_to_ror_stage(
            reconciled=reconciled, provider=_StubProvider(hits={}),
        ),
    )
    assert result.placeholders_examined == 0


def test_stage_returns_zero_when_provider_missing(monkeypatch):
    """No provider configured (Qdrant absent) — stage returns a sane
    result and never raises.

    Passing ``provider=None`` makes the stage fall back to
    ``build_default_provider()``. We force that to return ``None`` so the
    test deterministically exercises the provider-unavailable branch
    *without* constructing a real Qdrant client — otherwise the stage
    would issue a live ROR-RAG query and fail on DNS in a
    network-isolated CI sandbox (the production call-site in api.py wraps
    this stage in try/except, so a real outage degrades gracefully there).
    """
    monkeypatch.setattr(
        "git_metadata_extractor.pipeline.stages.resolve_placeholder_orgs_to_ror.build_default_provider",
        lambda *a, **k: None,
    )
    placeholder = _placeholder_org("u1", "EPFL")
    reconciled = ReconciledEntities(
        entities={"organizations": [placeholder], "memberships": []},
    )
    result = asyncio.run(
        run_resolve_placeholder_orgs_to_ror_stage(
            reconciled=reconciled, provider=None,
        ),
    )
    # Result is well-formed even when provider building fails.
    assert isinstance(result, PlaceholderResolutionResult)
    assert result.placeholders_examined == 0
    assert result.rejection_reasons == {"provider_unavailable": 1}


# ---------------------------------------------------------------------------
# api.py env-flag wiring
# ---------------------------------------------------------------------------


def test_api_env_flag_defaults_on(monkeypatch):
    from git_metadata_extractor import api as v2_api

    monkeypatch.delenv("V2_RESOLVE_PLACEHOLDER_ORGS_TO_ROR", raising=False)
    assert v2_api._resolve_placeholder_orgs_to_ror_enabled() is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "n", "f"])
def test_api_env_flag_recognises_off_values(value, monkeypatch):
    from git_metadata_extractor import api as v2_api

    monkeypatch.setenv("V2_RESOLVE_PLACEHOLDER_ORGS_TO_ROR", value)
    assert v2_api._resolve_placeholder_orgs_to_ror_enabled() is False


def test_api_constant_and_export_are_in_place():
    from git_metadata_extractor import api as v2_api
    from git_metadata_extractor.pipeline import stages

    assert v2_api.STAGE_RESOLVE_PLACEHOLDER_ORGS_TO_ROR == "resolve_placeholder_orgs_to_ror"
    assert callable(stages.run_resolve_placeholder_orgs_to_ror_stage)
    assert "run_resolve_placeholder_orgs_to_ror_stage" in stages.__all__
    assert "PlaceholderResolutionResult" in stages.__all__
