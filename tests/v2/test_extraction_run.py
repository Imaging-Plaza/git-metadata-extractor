"""Tests for the `pulse:ExtractionRun` descriptor.

First step of `PROVENANCE_ARCHITECTURE.md` phase 2, and the first production
use of the models generated from the v3 SHACL shapes — until now everything
under `schema/generated/` was generated and tested but unwired.

Two design decisions are asserted here rather than left to a comment, because
both are easy to "fix" into being wrong:

- The descriptor is **not** in `output["@graph"]`. Runs belong to the
  substrate/provenance layer; the canonical graph holds what was extracted, not
  how. Moving it inside would also make every corpus signature change.
- `pulse:extractedBy` is a **link**, and the agent is a sibling node. Nesting
  the agent inside the run would need a class IRI (`prov:SoftwareAgent`) used as
  a property name, which means nothing in RDF.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import Namespace

from git_metadata_extractor.pipeline.stages.extraction_run import (
    SOFTWARE_AGENT_IRI,
    build_extraction_run,
    run_iri,
    software_agent,
)

PULSE = Namespace("https://open-pulse.epfl.ch/ontology#")
PROV = Namespace("http://www.w3.org/ns/prov#")
SCHEMA = Namespace("http://schema.org/")

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTEXT_FILE = (
    REPO_ROOT / "git_metadata_extractor" / "schema" / "generated" / "context.jsonld"
)

STARTED = datetime(2026, 9, 8, 10, 0, 0, tzinfo=timezone.utc)
ENDED = STARTED + timedelta(seconds=22)


def _build(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "run_id": "abc-123",
        "seeds": ["https://github.com/octocat/Hello-World"],
        "started_at": STARTED,
        "ended_at": ENDED,
        "package_name": "git-metadata-extractor",
        "package_version": "3.0.0",
    }
    kwargs.update(overrides)
    return build_extraction_run(**kwargs)


def _nodes(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["@id"]: node for node in doc["@graph"]}


# --------------------------------------------------------------------------
# shape
# --------------------------------------------------------------------------


def test_descriptor_is_a_two_node_graph() -> None:
    nodes = _nodes(_build())

    assert set(nodes) == {run_iri("abc-123"), SOFTWARE_AGENT_IRI}
    assert nodes[run_iri("abc-123")]["@type"] == "pulse:ExtractionRun"
    assert nodes[SOFTWARE_AGENT_IRI]["@type"] == "prov:SoftwareAgent"


def test_agent_is_linked_by_iri_not_nested() -> None:
    run = _nodes(_build())[run_iri("abc-123")]

    assert run["pulse:extractedBy"] == SOFTWARE_AGENT_IRI
    # A class IRI must never appear as a property name.
    assert "prov:SoftwareAgent" not in run


def test_seeds_record_what_was_asked_for() -> None:
    doc = _build(seeds=["https://github.com/a/b", "https://github.com/c/d"])
    run = _nodes(doc)[run_iri("abc-123")]

    assert run["pulse:extractionSeed"] == [
        "https://github.com/a/b",
        "https://github.com/c/d",
    ]


def test_blank_seeds_are_dropped() -> None:
    """An empty seed would validate but says nothing."""
    run = _nodes(_build(seeds=["https://github.com/a/b", "", None]))[run_iri("abc-123")]

    assert run["pulse:extractionSeed"] == ["https://github.com/a/b"]


def test_ended_at_defaults_to_now() -> None:
    before = datetime.now(timezone.utc)
    run = _nodes(_build(ended_at=None))[run_iri("abc-123")]

    ended = datetime.fromisoformat(run["prov:endedAtTime"].replace("Z", "+00:00"))
    assert ended >= before


# --------------------------------------------------------------------------
# closed shapes reject typos at construction
# --------------------------------------------------------------------------


def test_unknown_property_is_rejected_by_the_generated_model() -> None:
    """Both provenance shapes are `sh:closed`.

    The value of validating here rather than at the SHACL gate is that a typo
    fails in the stage that made it, not three stages downstream as a warning.
    """
    from pydantic import ValidationError  # noqa: PLC0415

    from git_metadata_extractor.schema.generated.provenance import (  # noqa: PLC0415
        ExtractionRunModel,
    )

    with pytest.raises(ValidationError, match=r"[Ee]xtra"):
        ExtractionRunModel(**{"pulse:extractionSeeds": ["typo, plural"]})


def test_software_agent_carries_name_and_version() -> None:
    agent = software_agent(name="git-metadata-extractor", version="9.9.9")

    assert agent["schema:name"] == "git-metadata-extractor"
    assert agent["schema:softwareVersion"] == "9.9.9"


# --------------------------------------------------------------------------
# it has to survive the trip through RDF
# --------------------------------------------------------------------------


@pytest.mark.skipif(not CONTEXT_FILE.exists(), reason="context not generated")
def test_descriptor_expands_to_the_expected_triples() -> None:
    """End-to-end through the *generated* context, so both halves are checked.

    `pulse:extractedBy` must come out a `URIRef`. If the context lost its
    `@type: "@id"` the value would expand as a plain literal and the run would
    stop pointing at the agent — the same failure mode `pulse:ownedBy` had.
    """
    doc = _build()
    doc["@context"] = json.loads(CONTEXT_FILE.read_text(encoding="utf-8"))["@context"]
    graph = Graph().parse(data=json.dumps(doc), format="json-ld")

    run = URIRef(run_iri("abc-123"))
    agent = URIRef(SOFTWARE_AGENT_IRI)

    assert (run, PULSE.extractedBy, agent) in graph
    assert isinstance(graph.value(run, PULSE.extractedBy), URIRef)
    assert (agent, SCHEMA.name, Literal("git-metadata-extractor")) in graph

    started = graph.value(run, PROV.startedAtTime)
    assert started.toPython() == STARTED
    # The seed is a literal, not a link: it is what the caller typed.
    assert isinstance(graph.value(run, PULSE.extractionSeed), Literal)


# --------------------------------------------------------------------------
# the stage, and where its output lands
# --------------------------------------------------------------------------


def test_stage_records_the_run_outside_the_canonical_graph() -> None:
    """The corpus signature reads `output["@graph"]`; this must not touch it."""
    import asyncio  # noqa: PLC0415

    from git_metadata_extractor.agents.runtime import AgentRuntime  # noqa: PLC0415
    from git_metadata_extractor.pipeline.run import PAYLOAD_CHAIN  # noqa: PLC0415
    from git_metadata_extractor.pipeline.runner import (  # noqa: PLC0415
        Stage,
        run_pipeline,
    )
    from git_metadata_extractor.pipeline.state import PipelineState  # noqa: PLC0415

    stage: Stage = next(s for s in PAYLOAD_CHAIN if s.name == "extraction_run")

    class _Detected:
        value = "repository"

    class _Classification:
        detected_type = _Detected()
        normalized_url = "https://github.com/octocat/Hello-World"

    state = PipelineState(
        run_id="run-1",
        classification=_Classification(),
        runtime=AgentRuntime.RULE_BASED,
        providers=None,
        started_at=STARTED,
    )
    state.payload = {"@graph": [{"@id": "https://github.com/octocat/Hello-World"}]}

    asyncio.run(run_pipeline(state, [stage]))

    doc = state.extras["extraction_run"]
    assert _nodes(doc).keys() == {run_iri("run-1"), SOFTWARE_AGENT_IRI}
    # The graph the caller receives is untouched.
    assert state.payload["@graph"] == [
        {"@id": "https://github.com/octocat/Hello-World"},
    ]


def test_stage_fails_open() -> None:
    """A broken descriptor must not cost the caller an otherwise-good graph."""
    from git_metadata_extractor.pipeline.run import PAYLOAD_CHAIN  # noqa: PLC0415

    stage = next(s for s in PAYLOAD_CHAIN if s.name == "extraction_run")

    assert stage.fail_open is True
