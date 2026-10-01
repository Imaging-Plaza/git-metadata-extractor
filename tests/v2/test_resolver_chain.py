"""`RESOLVER_CHAIN` — the four ROR resolvers, as `/v2/extract` runs them.

Each resolver's own behaviour is tested in its `test_resolve_*` file. What
none of those can see is what the chain adds: the order the stages run in,
that each one fails open, and the name each one reports under — which is
the prefix of the warning a caller receives in the response when a stage
fails (`"<name> stage failed: <exc>"`).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from git_metadata_extractor.agents.runtime import AgentRuntime
from git_metadata_extractor.pipeline.run import RESOLVER_CHAIN
from git_metadata_extractor.pipeline.runner import run_pipeline
from git_metadata_extractor.pipeline.stages.models import ReconciledEntities
from git_metadata_extractor.pipeline.state import PipelineState


class _UnreachableRor:
    """A ROR RAG that is down — the case the corpus baseline records as
    `Name or service not known` on every resolver."""

    async def search(self, **_kwargs: Any) -> list[dict[str, Any]]:
        msg = "ror unreachable"
        raise OSError(msg)


def test_unreachable_ror_degrades_each_resolver_to_a_named_warning(monkeypatch):
    for flag in (
        "V2_RESOLVE_COMPANY_TO_ROR",
        "V2_RESOLVE_BIO_TO_ROR",
        "V2_RESOLVE_BIO_TO_ROR_LLM",
        "V2_RESOLVE_PLACEHOLDER_ORGS_TO_ROR",
    ):
        monkeypatch.delenv(flag, raising=False)
    state = PipelineState(
        run_id="run-1",
        classification=None,
        runtime=AgentRuntime.RULE_BASED,
        providers=SimpleNamespace(ror_rag=_UnreachableRor(), github=None),
        reconciled=ReconciledEntities(
            entities={
                "persons": [
                    {"id": "p1", "_company": "EPFL"},
                    {"id": "p2", "_bio": "PhD student at EPFL"},
                ],
                "organizations": [
                    {
                        "id": "u1",
                        "type": "org:Organization",
                        "identifiers": {"pulse:ror": None, "uuid": "u1"},
                        "idSource": "uuid",
                        "schema:name": "EPFL",
                    },
                ],
                "memberships": [],
            },
        ),
    )

    asyncio.run(run_pipeline(state, RESOLVER_CHAIN))

    # Rule-based runtime: the LLM resolver is gated off, so it neither runs
    # nor warns; the other three each fail open, in chain order.
    assert state.warnings == [
        "resolve_company_to_ror stage failed: ror unreachable",
        "resolve_bio_to_ror stage failed: ror unreachable",
        "resolve_placeholder_orgs_to_ror stage failed: ror unreachable",
    ]
