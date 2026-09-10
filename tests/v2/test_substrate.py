"""Tests for the substrate: the raw layer grouped into named graphs.

`PROVENANCE_ARCHITECTURE.md` phase 3. The substrate is the layer that makes
provenance possible at all — a named graph attributes the triples inside it to
whoever asserted them, so a canonical triple can later be traced back past
whatever normalisation the unifier applied to it.

**Almost nothing here is checkable by SHACL, and that is the point.** SHACL has
no notion of a graph name: it validates the union. So a node in the *wrong*
named graph conforms exactly as well as a node in the right one. Every routing
defect found while building this validated cleanly — a ROR organization
stranded beside the `ExtractionRun`, an article whose DOI id names a resolver
rather than a source, a membership with no anchor slot in its shape at all.
Conformance is measured separately (`scripts/v2/canonical_conformance.py
--layer substrate`, 119/119 with zero stranded entities); what is asserted here
is where things land.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from rdflib import Dataset, URIRef

from git_metadata_extractor.agents.runtime import AgentRuntime
from git_metadata_extractor.pipeline.run import PAYLOAD_CHAIN
from git_metadata_extractor.pipeline.runner import Stage, run_pipeline
from git_metadata_extractor.pipeline.stages.extraction_run import (
    SOFTWARE_AGENT_IRI,
    build_extraction_run,
    run_iri,
)
from git_metadata_extractor.pipeline.stages.raw_projection import (
    extraction_output_iri,
    project_raw,
)
from git_metadata_extractor.pipeline.stages.substrate import (
    build_substrate,
    group_by_output,
    meta_graph_iri,
    named_graph_sizes,
    to_nquads,
)
from git_metadata_extractor.pipeline.state import PipelineState
from git_metadata_extractor.schema import load_generated_context
from git_metadata_extractor.store import oxigraph
from git_metadata_extractor.validation import raw_shapes_available, validate_substrate
from git_metadata_extractor.validation.layers import Layer, LayerValidationResult

RUN_ID = "r1"
GITHUB_OUTPUT = extraction_output_iri(RUN_ID, "pulse:GitHub")
ROR_OUTPUT = extraction_output_iri(RUN_ID, "pulse:ROR")
INFOSCIENCE_OUTPUT = extraction_output_iri(RUN_ID, "pulse:Infoscience")
META = meta_graph_iri(RUN_ID)

STARS = 1497
EXPECTED_GITHUB_NODES = 5


@pytest.fixture
def flat_graph() -> list[dict[str, Any]]:
    """One repository's flat v2 graph, shaped like real corpus output.

    A GitHub org owning a repository, a synthesized owner Person, and the ROR
    organization the org resolved to — the combination that exposed the
    stranded-entity bug, because the ROR node is the only one with no platform.
    """
    return [
        {
            "@id": "https://github.com/ANTsX",
            "@type": "org:Organization",
            "schema:name": "ANTsX",
            "pulse:githubOrganizationHandle": "https://github.com/ANTsX",
            "org:unitOf": [{"@id": "https://ror.org/03zh00e46"}],
        },
        {
            "@id": "https://github.com/ANTsX/ANTs",
            "@type": "schema:SoftwareSourceCode",
            "schema:name": "ANTs",
            "pulse:githubRepositoryHandle": "https://github.com/ANTsX/ANTs",
            "pulse:githubRepoStars": STARS,
            "pulse:ownedBy": {"@id": "https://ror.org/03zh00e46"},
        },
        {
            "@id": "https://ror.org/03zh00e46",
            "@type": "org:Organization",
            "schema:name": "Normalization Housing Foundation",
            "org:hasUnit": [{"@id": "https://github.com/ANTsX"}],
            "pulse:owns": [{"@id": "https://github.com/ANTsX/ANTs"}],
        },
        {
            "@id": "urn:pulse:repo-author:ANTsX/ANTsX",
            "@type": "schema:Person",
            "schema:name": "ANTsX",
            "pulse:githubUsername": "https://github.com/ANTsX",
        },
    ]


def _substrate(nodes: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    return build_substrate(
        nodes,
        run_id=RUN_ID,
        context=load_generated_context(),
        **kwargs,
    )


def _graphs(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {entry["@id"]: entry["@graph"] for entry in doc["@graph"]}


def _ids(nodes: list[dict[str, Any]]) -> set[str]:
    return {node["@id"] for node in nodes}


# --------------------------------------------------------------------------
# document shape
# --------------------------------------------------------------------------


def test_the_document_is_json_ld_named_graphs(flat_graph: list[dict[str, Any]]) -> None:
    """An entry with both `@id` and `@graph` is a named graph in JSON-LD 1.1.

    Which is why the format is not an invented `{"@graphs": {...}}` envelope:
    this parses into an `rdflib.Dataset` with no translator, and the same bytes
    the response carries are the bytes the store receives.
    """
    doc = _substrate(flat_graph)

    assert "@context" in doc
    for entry in doc["@graph"]:
        assert set(entry) == {"@id", "@graph"}
        assert isinstance(entry["@graph"], list)


def test_one_graph_per_platform_slice(flat_graph: list[dict[str, Any]]) -> None:
    doc = _substrate(flat_graph)

    assert set(_graphs(doc)) == {GITHUB_OUTPUT, ROR_OUTPUT, META}


def test_empty_graphs_are_dropped(flat_graph: list[dict[str, Any]]) -> None:
    """A run that found nothing on a platform must not claim an empty slice."""
    doc = _substrate(flat_graph)

    assert INFOSCIENCE_OUTPUT not in _graphs(doc)
    assert all(entry["@graph"] for entry in doc["@graph"])


def test_grouping_loses_nothing(flat_graph: list[dict[str, Any]]) -> None:
    """Routing is a partition, not a filter.

    A node quietly dropped here would leave a dangling reference in the store
    with nothing to catch it — the raw entity shapes require no inbound edges.
    """
    raw = project_raw(flat_graph, run_id=RUN_ID)
    doc = _substrate(flat_graph)
    grouped = [node for nodes in _graphs(doc).values() for node in nodes]

    assert _ids(grouped) == _ids(raw["@graph"])
    assert len(grouped) == len(raw["@graph"])


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------


def test_entities_go_to_the_output_their_anchor_names(
    flat_graph: list[dict[str, Any]],
) -> None:
    graphs = _graphs(_substrate(flat_graph))

    assert "https://github.com/ANTsX/ANTs" in _ids(graphs[GITHUB_OUTPUT])
    assert "https://ror.org/03zh00e46" in _ids(graphs[ROR_OUTPUT])


def test_a_profile_goes_with_its_subject(flat_graph: list[dict[str, Any]]) -> None:
    """A profile is a statement *by* a platform about an entity.

    It carries no anchor of its own — `Raw*ProfileShape` is closed and declares
    no `pulse:partOfRun` — so it has to be routed by the subject it names.
    """
    graphs = _graphs(_substrate(flat_graph))
    github = _ids(graphs[GITHUB_OUTPUT])

    assert "urn:pulse:profile:github:ANTsX" in github
    assert "urn:pulse:org-profile:github:ANTsX" in github
    assert len(github) == EXPECTED_GITHUB_NODES


def test_a_membership_goes_with_the_person_who_holds_it() -> None:
    """The mirror-image case, and the one that needs a reverse index.

    A profile names its subject; a membership is named *by* its subject. Read
    only forwards and every membership strands — which SHACL cannot see,
    because `RawMembershipShape` requires nothing at all.
    """
    membership_id = "https://github.com/octocat__https://ror.org/02s376052"
    doc = _substrate(
        [
            {
                "@id": "https://github.com/octocat",
                "@type": "schema:Person",
                "schema:name": "The Octocat",
                "pulse:githubUsername": "https://github.com/octocat",
                "org:hasMembership": [{"@id": membership_id}],
            },
            {
                "@id": membership_id,
                "@type": "org:Membership",
                "org:organization": {"@id": "https://ror.org/02s376052"},
                "org:role": "Maintainer",
            },
        ],
    )
    graphs = _graphs(doc)

    assert membership_id in _ids(graphs[GITHUB_OUTPUT])
    assert membership_id not in _ids(graphs[META])


def test_the_meta_graph_holds_only_the_extraction_itself(
    flat_graph: list[dict[str, Any]],
) -> None:
    """An entity in the meta graph is a routing defect, not a category.

    This is the assertion the whole layer turns on: the meta graph describes
    *how* the extraction ran, and an entity there is a fact about the world
    filed as a fact about the run.
    """
    run_doc = build_extraction_run(
        run_id=RUN_ID,
        seeds=["https://github.com/ANTsX/ANTs"],
        started_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 9, 9, 12, 0, 22, tzinfo=timezone.utc),
        package_name="git-metadata-extractor",
        package_version="3.0.0",
    )
    doc = _substrate(flat_graph, run_nodes=run_doc["@graph"])
    meta_types = {str(node.get("@type")) for node in _graphs(doc)[META]}

    assert meta_types == {
        "pulse:ExtractionOutput",
        "pulse:ExtractionRun",
        "prov:SoftwareAgent",
    }


def test_group_by_output_is_callable_without_the_projection() -> None:
    """The routing rules stand alone, so they can be reasoned about alone."""
    grouped = group_by_output(
        [
            {"@id": GITHUB_OUTPUT, "@type": "pulse:ExtractionOutput"},
            {
                "@id": "https://github.com/a/b",
                "@type": "schema:SoftwareSourceCode",
                "pulse:partOfRun": {"@id": GITHUB_OUTPUT},
            },
            {"@id": "urn:pulse:orphan", "@type": "schema:Person"},
        ],
        meta_graph=META,
    )

    assert _ids(grouped[GITHUB_OUTPUT]) == {"https://github.com/a/b"}
    assert _ids(grouped[META]) == {GITHUB_OUTPUT, "urn:pulse:orphan"}


# --------------------------------------------------------------------------
# the run descriptor, and the reference that must resolve
# --------------------------------------------------------------------------


def test_outputs_link_to_the_run(flat_graph: list[dict[str, Any]]) -> None:
    doc = _substrate(flat_graph)
    outputs = [
        node
        for node in _graphs(doc)[META]
        if str(node.get("@type")) == "pulse:ExtractionOutput"
    ]

    assert outputs
    for output in outputs:
        assert output["prov:wasGeneratedBy"] == {"@id": run_iri(RUN_ID)}


def test_the_run_node_can_be_carried_so_the_reference_resolves(
    flat_graph: list[dict[str, Any]],
) -> None:
    """`ExtractionOutputShape` constrains `prov:wasGeneratedBy` with `sh:class`.

    A reference alone is a violation, not a forward declaration — the class can
    only be checked if the run node is in the graph being validated. Carrying
    it is also what makes a run's substrate self-describing once loaded.
    """
    run_doc = build_extraction_run(
        run_id=RUN_ID,
        seeds=["https://github.com/ANTsX/ANTs"],
        started_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
        package_name="git-metadata-extractor",
        package_version="3.0.0",
    )
    doc = _substrate(flat_graph, run_nodes=run_doc["@graph"])

    assert {run_iri(RUN_ID), SOFTWARE_AGENT_IRI} <= _ids(_graphs(doc)[META])


# --------------------------------------------------------------------------
# serialisation — what the store actually receives
# --------------------------------------------------------------------------


def test_nquads_round_trip_preserves_the_graph_names(
    flat_graph: list[dict[str, Any]],
) -> None:
    """The graph name is the provenance. Losing it loses the whole layer."""
    doc = _substrate(flat_graph)
    dataset = Dataset()
    dataset.parse(data=to_nquads(doc), format="nquads")

    populated = {
        str(context.identifier) for context in dataset.contexts() if len(context)
    }
    assert populated == set(_graphs(doc))


def test_nquads_carries_the_repository_stars_as_a_typed_literal(
    flat_graph: list[dict[str, Any]],
) -> None:
    """The generated context is what types the literals, so it has to be used.

    Serialising under the hand-written v2 context would emit the star count as
    a plain string, and `RawRepositoryShape` demands `xsd:integer`.
    """
    doc = _substrate(flat_graph)
    dataset = Dataset()
    dataset.parse(data=to_nquads(doc), format="nquads")

    stars = list(
        dataset.quads(
            (
                URIRef("https://github.com/ANTsX/ANTs"),
                URIRef("https://open-pulse.epfl.ch/ontology#repositoryStars"),
                None,
                None,
            ),
        ),
    )
    assert [str(quad[2]) for quad in stars] == [str(STARS)]
    assert stars[0][2].toPython() == STARS
    assert str(stars[0][3]) == GITHUB_OUTPUT


def test_named_graph_sizes_counts_nodes(flat_graph: list[dict[str, Any]]) -> None:
    doc = _substrate(flat_graph)
    sizes = named_graph_sizes(doc)

    assert sizes[GITHUB_OUTPUT] == EXPECTED_GITHUB_NODES
    assert sizes[ROR_OUTPUT] == 1
    assert sum(sizes.values()) == sum(len(nodes) for nodes in _graphs(doc).values())


def test_graph_iris_embed_the_run_so_the_substrate_is_append_only() -> None:
    """Two runs over the same repository must not overwrite each other.

    "Raw assertions, never rewritten" is enforced by the IRI, not by the
    writer: a second run writes beside the first because it addresses different
    graphs.
    """
    first = build_substrate([], run_id="run-a", context={})
    second = build_substrate([], run_id="run-b", context={})

    assert meta_graph_iri("run-a") != meta_graph_iri("run-b")
    assert extraction_output_iri("run-a", "pulse:GitHub") != extraction_output_iri(
        "run-b",
        "pulse:GitHub",
    )
    assert first["@graph"] == second["@graph"] == []


def test_an_empty_graph_serialises_to_no_quads() -> None:
    assert to_nquads({"@context": {}, "@graph": []}).strip() == ""


def test_the_context_is_the_generated_one_not_the_v2_file(
    flat_graph: list[dict[str, Any]],
) -> None:
    """The raw layer is v3, so it must be described by the v3 context.

    `pulse:githubRepoStars` does not exist in v3 — it is
    `pulse:repositoryStars` — so a v2 context would leave the term unexpanded
    and the triple absent from the store entirely.
    """
    doc = _substrate(flat_graph)
    serialised = json.dumps(doc["@context"])

    assert "repositoryStars" in serialised
    assert "githubRepoStars" not in serialised


# --------------------------------------------------------------------------
# the pipeline stages
# --------------------------------------------------------------------------


def _pipeline_state(**kwargs: Any) -> PipelineState:
    class _Detected:
        value = "repository"

    class _Classification:
        detected_type = _Detected()
        normalized_url = "https://github.com/ANTsX/ANTs"

    return PipelineState(
        run_id=RUN_ID,
        classification=_Classification(),
        runtime=AgentRuntime.RULE_BASED,
        providers=None,
        **kwargs,
    )


def _stage(name: str) -> Stage:
    return next(stage for stage in PAYLOAD_CHAIN if stage.name == name)


def _run(stages: list[Stage], state: PipelineState) -> PipelineState:
    return asyncio.run(run_pipeline(state, stages))


def test_the_projection_runs_before_the_canonical_one_overwrites_the_input() -> None:
    """Both layers project the *same* flat intermediate.

    `canonical_projection` replaces `state.payload["@graph"]` in place, so the
    substrate has to be built first. Reversed, the substrate would be a raw
    projection *of the canonical graph* — translated twice, and still valid,
    because the raw entity shapes are open.
    """
    names = [stage.name for stage in PAYLOAD_CHAIN]

    assert names.index("jsonld_build") < names.index("substrate_projection")
    assert names.index("substrate_projection") < names.index("canonical_projection")


def test_projecting_after_the_canonical_stage_degrades_the_substrate(
    monkeypatch: pytest.MonkeyPatch,
    flat_graph: list[dict[str, Any]],
) -> None:
    """What the ordering above actually prevents, shown rather than asserted.

    Run the wrong way round, the substrate is a raw projection of the
    *canonical* graph: `_project_repository` reads `pulse:githubRepositoryHandle`
    and the person projector reads `pulse:githubUsername`, both of which v3
    renamed. So the repository loses its handle and its platform, the person
    loses its profile, and nothing complains — the raw entity shapes are open
    and require none of it. A silently thinner substrate is the failure mode,
    not an error.
    """
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    state = _pipeline_state()
    state.payload = {"@graph": flat_graph}

    _run([_stage("canonical_projection"), _stage("substrate_projection")], state)

    assert state.substrate is not None
    graphs = _graphs(state.substrate)

    # The GitHub slice loses two of its five nodes: the person's profile is
    # never minted, and the person itself strands in the meta graph with only
    # a name — no profile, no anchor, nothing saying where it came from.
    assert len(graphs[GITHUB_OUTPUT]) < EXPECTED_GITHUB_NODES
    assert "urn:pulse:profile:github:ANTsX" not in _ids(graphs[GITHUB_OUTPUT])
    assert "urn:pulse:repo-author:ANTsX/ANTsX" in _ids(graphs[META])

    # And the repository keeps its id-derived platform while losing everything
    # the v2 property names carried.
    repo = next(
        node
        for node in graphs[GITHUB_OUTPUT]
        if node["@id"] == "https://github.com/ANTsX/ANTs"
    )
    assert "pulse:repositoryHandle" not in repo
    assert "pulse:repositoryStars" not in repo


def test_the_write_runs_after_the_run_descriptor_exists() -> None:
    """`extraction_run` mints the run node last, so the write must follow it.

    Written earlier, the substrate's outputs would reference an
    `ExtractionRun` absent from the payload — a dangling reference rather than
    a forward declaration, since `ExtractionOutputShape` constrains
    `prov:wasGeneratedBy` with `sh:class`.
    """
    names = [stage.name for stage in PAYLOAD_CHAIN]

    assert names.index("extraction_run") < names.index("substrate_write")
    assert names[-1] == "substrate_write"


def test_both_stages_fail_open() -> None:
    """The substrate is additive and unconsumed until phase 4.

    A broken projection or an unreachable store must cost a warning, not the
    graph the caller asked for.
    """
    assert _stage("substrate_projection").fail_open is True
    assert _stage("substrate_write").fail_open is True


def test_both_stages_are_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("V2_SUBSTRATE_ENABLED", raising=False)
    state = _pipeline_state()

    assert _stage("substrate_projection").applies(state) is False
    assert _stage("substrate_write").applies(state) is False


def test_the_projection_stage_populates_state_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    flat_graph: list[dict[str, Any]],
) -> None:
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    state = _pipeline_state()
    state.payload = {"@graph": flat_graph}

    _run([_stage("substrate_projection")], state)

    assert state.substrate is not None
    assert set(_graphs(state.substrate)) == {GITHUB_OUTPUT, ROR_OUTPUT, META}
    assert state.extras["substrate_graph_sizes"][GITHUB_OUTPUT] == EXPECTED_GITHUB_NODES
    # The graph the caller receives is untouched by the projection.
    assert state.payload["@graph"] == flat_graph


def test_the_write_stage_folds_the_run_descriptor_into_the_meta_graph(
    monkeypatch: pytest.MonkeyPatch,
    flat_graph: list[dict[str, Any]],
) -> None:
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    monkeypatch.delenv("V2_SUBSTRATE_STORE_URL", raising=False)
    state = _pipeline_state(started_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc))
    state.payload = {"@graph": flat_graph}

    _run(
        [
            _stage("substrate_projection"),
            _stage("extraction_run"),
            _stage("substrate_write"),
        ],
        state,
    )

    assert state.substrate is not None
    assert {run_iri(RUN_ID), SOFTWARE_AGENT_IRI} <= _ids(_graphs(state.substrate)[META])
    # No store configured: projected and returned, written nowhere.
    assert "substrate_quads_written" not in state.extras
    assert state.warnings == []


def test_the_write_stage_posts_the_quads_when_a_store_is_configured(
    monkeypatch: pytest.MonkeyPatch,
    flat_graph: list[dict[str, Any]],
) -> None:
    """The one place the projection and the client meet."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    real_from_config = oxigraph.store_from_config
    monkeypatch.setattr(
        oxigraph,
        "store_from_config",
        lambda url, **kwargs: real_from_config(
            url,
            **{**kwargs, "transport": httpx.MockTransport(handler)},
        ),
    )
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    monkeypatch.setenv("V2_SUBSTRATE_STORE_URL", "http://oxigraph:7878")

    state = _pipeline_state(started_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc))
    state.payload = {"@graph": flat_graph}

    _run(
        [
            _stage("substrate_projection"),
            _stage("extraction_run"),
            _stage("substrate_write"),
        ],
        state,
    )

    assert len(seen) == 1
    body = seen[0].content.decode()
    assert f"<{GITHUB_OUTPUT}> ." in body
    assert f"<{ROR_OUTPUT}> ." in body
    assert state.extras["substrate_quads_written"] == len(
        [line for line in body.splitlines() if line.strip()],
    )
    assert state.extras["substrate_store_url"] == "http://oxigraph:7878"


def test_an_unreachable_store_warns_and_keeps_the_graph(
    monkeypatch: pytest.MonkeyPatch,
    flat_graph: list[dict[str, Any]],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message)

    real_from_config = oxigraph.store_from_config
    monkeypatch.setattr(
        oxigraph,
        "store_from_config",
        lambda url, **kwargs: real_from_config(
            url,
            **{**kwargs, "transport": httpx.MockTransport(handler)},
        ),
    )
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    monkeypatch.setenv("V2_SUBSTRATE_STORE_URL", "http://oxigraph:7878")

    state = _pipeline_state()
    state.payload = {"@graph": flat_graph}

    _run([_stage("substrate_projection"), _stage("substrate_write")], state)

    assert any("substrate_write" in warning for warning in state.warnings)
    assert state.payload["@graph"] == flat_graph
    assert state.substrate is not None


def test_a_missing_run_descriptor_is_warned_about_not_written_silently(
    monkeypatch: pytest.MonkeyPatch,
    flat_graph: list[dict[str, Any]],
) -> None:
    """`extraction_run` is fail-open, so it can leave nothing behind.

    The substrate's outputs already carry `prov:wasGeneratedBy` → the run IRI,
    which `run_iri(run_id)` determines without the descriptor. So a substrate
    written without it is not malformed JSON — it is a slice whose run node is
    absent from the store, and `ExtractionOutputShape` constrains that
    reference with `sh:class`. Unattributable to any run is the one state this
    layer must not reach quietly.
    """
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    monkeypatch.delenv("V2_SUBSTRATE_STORE_URL", raising=False)
    state = _pipeline_state()
    state.payload = {"@graph": flat_graph}

    # No `extraction_run` stage in the chain.
    _run([_stage("substrate_projection"), _stage("substrate_write")], state)

    assert any("extraction run descriptor" in warning for warning in state.warnings)
    assert state.substrate is not None
    assert run_iri(RUN_ID) not in _ids(_graphs(state.substrate)[META])


def test_a_deposit_is_routed_with_its_article() -> None:
    """A `pulse:Deposit` has no anchor of its own, like a profile.

    And it names its subject the other way round again — `pulse:depositOf`.
    Missing from `_NAMES_SUBJECT`, every deposit stranded in the meta graph,
    and the only symptom was that the article's `pulse:hasDeposit` pointed into
    a different named graph — which SHACL, validating the union, cannot see.
    """
    doc = _substrate(
        [
            {
                "@id": "https://doi.org/10.1093/mnras/stad3265",
                "@type": "schema:ScholarlyArticle",
                "schema:name": "Probing the roles of orientation",
                "schema:datePublished": "2023-12-23",
                "pulse:infoscienceArticleIdentifier": (
                    "https://infoscience.epfl.ch/entities/publication/78b678d1"
                ),
            },
        ],
    )
    graphs = _graphs(doc)
    infoscience = _ids(graphs[INFOSCIENCE_OUTPUT])

    assert "https://doi.org/10.1093/mnras/stad3265" in infoscience
    assert any(iri.startswith("urn:pulse:deposit:") for iri in infoscience)
    assert not any(iri.startswith("urn:pulse:deposit:") for iri in _ids(graphs[META]))


# --------------------------------------------------------------------------
# the substrate's validation gate (phase 6, reporting half)
# --------------------------------------------------------------------------


def test_the_substrate_write_reports_violations_without_refusing(
    monkeypatch: pytest.MonkeyPatch,
    flat_graph: list[dict[str, Any]],
) -> None:
    """A malformed slice still gets written, and warns.

    The substrate is append-only and the only durable copy of what the run
    found, so refusing over one bad entity loses the rest of it permanently.
    The enforcing gate is on the canonical side, where refusing leaves the
    previous valid graph answering queries.
    """
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    monkeypatch.delenv("V2_SUBSTRATE_STORE_URL", raising=False)
    monkeypatch.setattr(
        "git_metadata_extractor.validation.validate_substrate",
        lambda _document: LayerValidationResult(
            layer=Layer.SUBSTRATE,
            conforms=False,
            violations=[{"message": "made up"}],
            triples=7,
        ),
    )
    state = _pipeline_state()
    state.payload = {"@graph": flat_graph}

    _run([_stage("substrate_projection"), _stage("substrate_write")], state)

    assert any("SHACL violation" in warning for warning in state.warnings)
    # The substrate was still produced.
    assert state.substrate is not None
    assert state.extras["substrate_validation"]


def test_substrate_validation_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch,
    flat_graph: list[dict[str, Any]],
) -> None:
    """For a bulk backfill where the SHACL pass dominates the run."""
    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    monkeypatch.setenv("V2_SUBSTRATE_VALIDATE", "false")
    monkeypatch.delenv("V2_SUBSTRATE_STORE_URL", raising=False)
    state = _pipeline_state()
    state.payload = {"@graph": flat_graph}

    _run([_stage("substrate_projection"), _stage("substrate_write")], state)

    assert "substrate_validation" not in state.extras


def test_the_real_substrate_slice_conforms(
    monkeypatch: pytest.MonkeyPatch,
    flat_graph: list[dict[str, Any]],
) -> None:
    """Not a mock: the written document against the real raw shapes.

    Measured over the whole corpus this is 119/119, but a per-slice assertion
    catches a projection change before a corpus run does.

    The **written** document, not the projection's: `substrate_projection`
    deliberately leaves the run descriptor out, because `extraction_run` mints
    it later — so the intermediate has an `ExtractionOutput` whose
    `prov:wasGeneratedBy` names a run node not yet in the graph, which is a
    `sh:class` violation. That is exactly why validation sits in
    `substrate_write`, after the descriptor is folded in, and why the first
    version of this test failed.
    """
    if not raw_shapes_available():
        pytest.skip("ontology submodule not prepared")

    monkeypatch.setenv("V2_SUBSTRATE_ENABLED", "true")
    monkeypatch.delenv("V2_SUBSTRATE_STORE_URL", raising=False)
    state = _pipeline_state(started_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc))
    state.payload = {"@graph": flat_graph}
    _run(
        [
            _stage("substrate_projection"),
            _stage("extraction_run"),
            _stage("substrate_write"),
        ],
        state,
    )

    assert state.substrate is not None
    result = validate_substrate(state.substrate)

    assert result.conforms, result.violations
    # And the stage reached the same verdict on the way past.
    assert "conforms=True" in state.extras["substrate_validation"]
