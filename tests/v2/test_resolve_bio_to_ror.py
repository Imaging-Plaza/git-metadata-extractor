"""Tests for `resolve_bio_to_ror` stage + its `api.py` wiring.

The stage is the deterministic Stage A of the "infer affiliation from
richer Person signals" track. Stage B (LLM agent) lives behind a
separate flag and is tested in its own file when it lands.

Coverage focus:
  - Pure helpers (`_extract_bio_candidates`, `_blog_to_query`).
  - Stage entry: bio strings, ORCID bios, and blog domain hints each
    drive a resolution; missing signals → no-op; non-org candidates
    rejected without a search.
  - `skip_if_already_affiliated`: defaults True; can additively enrich
    when False.
  - All three key shapes (`_bio` / `gme-internal:bio` / full IRI).
  - The `V2_RESOLVE_BIO_TO_ROR` env flag — defaults on, opt-out off.
  - Stub provider — never hits Qdrant.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.v2.pipeline.stages.models import ReconciledEntities
from src.v2.pipeline.stages.resolve_bio_to_ror import (
    BIO_KEYS,
    BLOG_KEYS,
    DOMAIN_HINTS,
    ORCID_BIO_KEYS,
    BioAffiliationResult,
    _blog_to_query,
    _extract_bio_candidates,
    run_resolve_bio_to_ror_stage,
)
from src.v2.pipeline.stages.resolve_company_to_ror import SCHEMA_AFFILIATION


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_extract_bio_candidates_pulls_at_phrase():
    cands = _extract_bio_candidates("Research Engineer at Google DeepMind")
    assert "Google DeepMind" in cands


def test_extract_bio_candidates_pulls_handle():
    cands = _extract_bio_candidates("Hacking on diffusion models @DeepMind")
    assert "DeepMind" in cands


def test_extract_bio_candidates_pulls_comma_phrase_after_role():
    cands = _extract_bio_candidates("PhD candidate, ETH Zurich.")
    assert "ETH Zurich" in cands


def test_extract_bio_candidates_pulls_from_phrase():
    cands = _extract_bio_candidates("ML researcher, PhD from MIT")
    # Both the `, MIT`-ish pattern and `from MIT` may match; we just
    # need MIT in the candidate set somewhere.
    assert any("MIT" in c for c in cands)


def test_extract_bio_candidates_filters_stopwords():
    """Single-word stopword captures must be dropped — they would
    waste a vector-search round trip otherwise."""
    cands = _extract_bio_candidates("@PhD, @ML, @Research")
    # All three captures match a stopword, so nothing should leak through.
    assert cands == []


def test_extract_bio_candidates_returns_empty_on_no_signal():
    assert _extract_bio_candidates("") == []
    assert _extract_bio_candidates("just rambling here") == []


def test_extract_bio_candidates_dedups_case_insensitive():
    cands = _extract_bio_candidates("at MIT. From MIT.")
    lowered = [c.lower() for c in cands]
    assert lowered.count("mit") == 1


def test_blog_to_query_matches_known_domain():
    assert _blog_to_query("https://people.epfl.ch/some.person") == "EPFL"


def test_blog_to_query_handles_missing_scheme():
    assert _blog_to_query("ethz.ch/~someone") == "ETH Zurich"


def test_blog_to_query_walks_subdomains():
    """`lab.compbio.epfl.ch` should fall through to `epfl.ch`."""
    assert _blog_to_query("https://lab.compbio.epfl.ch") == "EPFL"


def test_blog_to_query_returns_none_on_unknown_host():
    assert _blog_to_query("https://example.com/blog") is None


def test_blog_to_query_returns_none_on_blank_or_garbage():
    assert _blog_to_query(None) is None
    assert _blog_to_query("") is None
    assert _blog_to_query("not a url") is None


def test_domain_hints_keys_are_lowercase():
    """Hostnames from urlparse are lowercased, so the lookup table
    must match — otherwise a perfectly good entry silently misses."""
    for key in DOMAIN_HINTS:
        assert key == key.lower(), f"DOMAIN_HINTS key {key!r} must be lowercase"


# ---------------------------------------------------------------------------
# Stage entry with a stub provider
# ---------------------------------------------------------------------------


class _StubProvider:
    """Lookup table: cleaned query → list of hit dicts. Mirrors the
    company-stage test stub so we exercise the same `_resolve_one`
    code path with the same interface."""

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


def _run(reconciled: ReconciledEntities, provider: Any, **kwargs: Any) -> BioAffiliationResult:
    return asyncio.run(
        run_resolve_bio_to_ror_stage(
            reconciled=reconciled, provider=provider, **kwargs,
        ),
    )


def test_stage_resolves_from_bio_at_phrase():
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", "_bio": "Senior Research Engineer at Google DeepMind"},
            ],
        },
    )
    provider = _StubProvider(
        hits={
            "Google DeepMind": [
                {"score": 0.93, "types": ["company"], "name": "Google DeepMind", "ror_id": "https://ror.org/deepmind"},
                {"score": 0.50, "types": ["company"], "name": "Google"},
            ],
        },
    )
    result = _run(reconciled, provider)
    assert reconciled.entities["persons"][0][SCHEMA_AFFILIATION] == "https://ror.org/deepmind"
    assert result.persons_resolved == 1
    assert result.queries_accepted == 1


def test_stage_resolves_from_orcid_bio():
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", "_orcid_biography": "Postdoc at EPFL since 2022."},
            ],
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
    _run(reconciled, provider)
    assert reconciled.entities["persons"][0][SCHEMA_AFFILIATION] == "https://ror.org/02s376052"


def test_stage_resolves_from_blog_domain():
    """Blog domain alone (no bio) should still pull an affiliation."""
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", "_blog": "https://people.epfl.ch/jane.doe"},
            ],
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
    _run(reconciled, provider)
    assert reconciled.entities["persons"][0][SCHEMA_AFFILIATION] == "https://ror.org/02s376052"


def test_stage_no_op_when_no_signals():
    reconciled = ReconciledEntities(
        entities={"persons": [{"id": "p1"}, {"id": "p2", "_bio": ""}]},
    )
    provider = _StubProvider(hits={})
    result = _run(reconciled, provider)
    assert provider.queries == []
    assert result.persons_resolved == 0


def test_stage_skips_already_affiliated_by_default():
    """The stage is a backstop — when company-stage already stamped
    an affiliation, the bio stage stays out of the way."""
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {
                    "id": "p1",
                    "_bio": "Now at Google DeepMind",
                    SCHEMA_AFFILIATION: "https://ror.org/companyx",
                },
            ],
        },
    )
    provider = _StubProvider(hits={})
    _run(reconciled, provider)
    # Untouched: still just the company-stage value, no queries issued.
    assert reconciled.entities["persons"][0][SCHEMA_AFFILIATION] == "https://ror.org/companyx"
    assert provider.queries == []


def test_stage_enriches_when_skip_disabled():
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {
                    "id": "p1",
                    "_bio": "Now at Google DeepMind",
                    SCHEMA_AFFILIATION: "https://ror.org/companyx",
                },
            ],
        },
    )
    provider = _StubProvider(
        hits={
            "Google DeepMind": [
                {"score": 0.93, "types": ["company"], "name": "Google DeepMind", "ror_id": "https://ror.org/deepmind"},
                {"score": 0.40, "types": ["company"], "name": "Other"},
            ],
        },
    )
    _run(reconciled, provider, skip_if_already_affiliated=False)
    affiliations = reconciled.entities["persons"][0][SCHEMA_AFFILIATION]
    assert isinstance(affiliations, list)
    assert "https://ror.org/companyx" in affiliations
    assert "https://ror.org/deepmind" in affiliations


@pytest.mark.parametrize("bio_key", BIO_KEYS)
def test_stage_reads_bio_under_every_key_shape(bio_key):
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", bio_key: "Research Engineer at Google DeepMind"},
            ],
        },
    )
    provider = _StubProvider(
        hits={
            "Google DeepMind": [
                {"score": 0.93, "types": ["company"], "name": "Google DeepMind", "ror_id": "https://ror.org/deepmind"},
                {"score": 0.40, "types": ["company"], "name": "Other"},
            ],
        },
    )
    _run(reconciled, provider)
    assert reconciled.entities["persons"][0][SCHEMA_AFFILIATION] == "https://ror.org/deepmind"


@pytest.mark.parametrize("blog_key", BLOG_KEYS)
def test_stage_reads_blog_under_every_key_shape(blog_key):
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", blog_key: "https://people.epfl.ch/x"},
            ],
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
    _run(reconciled, provider)
    assert reconciled.entities["persons"][0][SCHEMA_AFFILIATION] == "https://ror.org/02s376052"


@pytest.mark.parametrize("orcid_key", ORCID_BIO_KEYS)
def test_stage_reads_orcid_bio_under_every_key_shape(orcid_key):
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", orcid_key: "PhD candidate, ETH Zurich"},
            ],
        },
    )
    provider = _StubProvider(
        hits={
            "ETH Zurich": [
                {"score": 0.95, "types": ["education"], "name": "ETH Zurich", "ror_id": "https://ror.org/05a28rw58"},
                {"score": 0.40, "types": ["education"], "name": "Other"},
            ],
        },
    )
    _run(reconciled, provider)
    assert reconciled.entities["persons"][0][SCHEMA_AFFILIATION] == "https://ror.org/05a28rw58"


def test_stage_dedups_candidates_across_sources():
    """When bio + blog point at the same institution, we only issue
    one search (cached by cleaned query)."""
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {
                    "id": "p1",
                    "_bio": "PhD candidate, EPFL",
                    "_blog": "https://people.epfl.ch/x",
                },
            ],
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
    _run(reconciled, provider)
    # Cleaned query "EPFL" is asked once, then served from the cache.
    assert provider.queries.count("EPFL") == 1


def test_stage_rejects_low_confidence_bio_candidate():
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", "_bio": "Engineer at Something"},
            ],
        },
    )
    provider = _StubProvider(
        hits={
            "Something": [
                {"score": 0.40, "types": ["company"], "name": "Something Co"},
            ],
        },
    )
    result = _run(reconciled, provider)
    assert SCHEMA_AFFILIATION not in reconciled.entities["persons"][0]
    assert result.persons_resolved == 0


def test_stage_is_idempotent_on_re_run_unaffiliated():
    """Person carries no prior affiliation. Re-running after a first
    run leaves the resolved affiliation unchanged."""
    reconciled = ReconciledEntities(
        entities={
            "persons": [
                {"id": "p1", "_bio": "Research Engineer at Google DeepMind"},
            ],
        },
    )
    provider = _StubProvider(
        hits={
            "Google DeepMind": [
                {"score": 0.93, "types": ["company"], "name": "Google DeepMind", "ror_id": "https://ror.org/deepmind"},
                {"score": 0.40, "types": ["company"], "name": "Other"},
            ],
        },
    )
    # First run skips no one (no prior affiliation). Second run hits
    # the skip_if_already_affiliated guard and short-circuits.
    _run(reconciled, provider)
    first = reconciled.entities["persons"][0][SCHEMA_AFFILIATION]
    queries_after_first = len(provider.queries)
    _run(reconciled, provider)
    second = reconciled.entities["persons"][0][SCHEMA_AFFILIATION]
    assert first == second
    assert len(provider.queries) == queries_after_first  # skip path → no new query


def test_stage_returns_zero_when_provider_missing():
    reconciled = ReconciledEntities(entities={"persons": [{"id": "p1"}]})
    result = asyncio.run(
        run_resolve_bio_to_ror_stage(reconciled=reconciled, provider=None),
    )
    assert isinstance(result, BioAffiliationResult)


# ---------------------------------------------------------------------------
# api.py env-flag wiring
# ---------------------------------------------------------------------------


def test_api_env_flag_defaults_on(monkeypatch):
    from src.v2 import api as v2_api

    monkeypatch.delenv("V2_RESOLVE_BIO_TO_ROR", raising=False)
    assert v2_api._resolve_bio_to_ror_enabled() is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", "n", "f"])
def test_api_env_flag_recognises_off_values(value, monkeypatch):
    from src.v2 import api as v2_api

    monkeypatch.setenv("V2_RESOLVE_BIO_TO_ROR", value)
    assert v2_api._resolve_bio_to_ror_enabled() is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "anything-else"])
def test_api_env_flag_treats_other_values_as_on(value, monkeypatch):
    from src.v2 import api as v2_api

    monkeypatch.setenv("V2_RESOLVE_BIO_TO_ROR", value)
    assert v2_api._resolve_bio_to_ror_enabled() is True


def test_api_constant_and_export_are_in_place():
    from src.v2 import api as v2_api
    from src.v2.pipeline import stages

    assert v2_api.STAGE_RESOLVE_BIO_TO_ROR == "resolve_bio_to_ror"
    assert callable(stages.run_resolve_bio_to_ror_stage)
    assert "run_resolve_bio_to_ror_stage" in stages.__all__
    assert "BioAffiliationResult" in stages.__all__
