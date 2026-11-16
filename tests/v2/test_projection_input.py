"""Source facts survive JSON-LD assembly without leaking private metadata."""

import asyncio
import json

import pytest

from git_metadata_extractor.pipeline.run import (
    _build_jsonld,
    _project_substrate,
    _record_extraction_run,
    _write_substrate,
)
from git_metadata_extractor.pipeline.stages.models import AssembledOutput
from git_metadata_extractor.pipeline.stages.raw_projection import (
    extraction_output_iri,
    project_raw,
)
from git_metadata_extractor.pipeline.state import PipelineState
from git_metadata_extractor.providers.detection.github_url_classifier import (
    classify_github_url,
)
from git_metadata_extractor.schema import load_jsonld_context
from git_metadata_extractor.validation import validate_substrate

REPOSITORY = "https://github.com/example/tool"


def make_state(*, debug=False):
    return PipelineState(
        run_id="source-facts",
        classification=classify_github_url(REPOSITORY),
        runtime="rule_based",
        providers=None,
        read_only=True,
        include_internal_fields=debug,
        jsonld_context=load_jsonld_context(),
        assembled=AssembledOutput(
            root_entity={
                "id": REPOSITORY,
                "type": "schema:SoftwareSourceCode",
                "schema:name": "tool",
                "pulse:githubRepositoryHandle": REPOSITORY,
                "schema:author": ["alice"],
                "_archived": False,
                "_subscribers_count": 0,
                "_description": "A source description",
                "_homepage": "https://example.org/tool",
                "_default_branch": "main",
                "_unknown": {"_nested": "private sentinel"},
            },
            related_entities=[{
                "id": "alice",
                "type": "schema:Person",
                "schema:name": "Alice",
                "pulse:githubUsername": "https://github.com/alice",
                "pulse:infosciencePersonIdentifier": "https://infoscience.epfl.ch/entities/person/123",
                "_bio": "GitHub biography",
                "_orcid_biography": "ORCID biography",
                "_followers_count": 0,
            }, {
                "id": "https://github.com/example",
                "type": "org:Organization",
                "schema:name": "Example",
                "pulse:githubOrganizationHandle": "https://github.com/example",
                "pulse:infoscienceOrganizationIdentifier": "https://infoscience.epfl.ch/entities/orgunit/456",
                "_description": "GitHub organization",
                "_followers_count": 0,
            }],
        ),
    )


def source_nodes(state, platform):
    graph_id = extraction_output_iri(state.run_id, platform)
    return next(graph["@graph"] for graph in state.substrate["@graph"] if graph["@id"] == graph_id)


@pytest.mark.parametrize("debug", [False, True])
def test_pipeline_preserves_source_facts_without_persisting_internal_fields(debug):
    state = make_state(debug=debug)
    _build_jsonld(state)
    public_graph = state.payload["@graph"]
    assert "_unknown" not in json.dumps(public_graph)
    assert ("private sentinel" in json.dumps(public_graph)) is debug

    _project_substrate(state)
    github = source_nodes(state, "pulse:GitHub")
    repo = next(node for node in github if node["@id"] == REPOSITORY)
    assert repo["pulse:archived"] is False
    assert repo["pulse:watcherCount"] == 0
    assert repo["schema:description"] == "A source description"
    assert repo["schema:url"] == {"@id": "https://example.org/tool"}
    assert repo["pulse:defaultBranch"] == "main"
    assert repo["schema:author"] == [{"@id": "urn:pulse:alice"}]
    assert any(node["@id"] == "urn:pulse:alice" for node in github)

    serialized = json.dumps(state.substrate)
    assert "private sentinel" not in serialized
    assert "gme-internal:" not in serialized
    assert "_subscribers_count" not in serialized
    assert state.projection_input
    assert "projection_input" not in state.model_dump()
    _record_extraction_run(state)
    asyncio.run(_write_substrate(state))
    result = validate_substrate(state.substrate)
    assert not result.skipped
    assert result.conforms, result.violations


def test_github_profile_facts_are_not_attributed_to_infoscience():
    state = make_state()
    _build_jsonld(state)
    _project_substrate(state)
    github = source_nodes(state, "pulse:GitHub")
    person = next(node for node in github if node["@type"] == "pulse:PlatformProfile")
    assert person["pulse:biography"] == "GitHub biography"
    assert person["pulse:followerCount"] == 0
    organization = next(node for node in github if node["@type"] == "pulse:OrganizationProfile")
    assert organization["schema:description"] == "GitHub organization"
    assert organization["pulse:followerCount"] == 0
    profiles = [node for node in source_nodes(state, "pulse:Infoscience")
                if node["@type"] in {"pulse:PlatformProfile", "pulse:OrganizationProfile"}]
    assert {node["@type"] for node in profiles} == {"pulse:PlatformProfile", "pulse:OrganizationProfile"}
    for profile in profiles:
        assert not {"pulse:biography", "pulse:followerCount", "schema:description"} & profile.keys()


def test_raw_source_links_do_not_resolve_bare_values_against_the_working_directory():
    state = make_state()
    state.assembled.root_entity["_homepage"] = "example.org"
    for entity in state.assembled.related_entities:
        entity["_blog"] = "example.org"
        entity["_twitter_username"] = "alice"
    _build_jsonld(state)
    _project_substrate(state)
    for node in source_nodes(state, "pulse:GitHub"):
        assert "schema:url" not in node
        assert "pulse:socialLink" not in node


def test_programming_language_is_not_a_natural_language():
    nodes = project_raw([{
        "@id": REPOSITORY,
        "@type": "schema:SoftwareSourceCode",
        "pulse:githubRepositoryHandle": REPOSITORY,
        "schema:programmingLanguage": ["Python"],
        "_primary_language": "Python",
    }], run_id="language")
    repo = next(node for node in nodes["@graph"] if node["@id"] == REPOSITORY)
    assert repo["schema:programmingLanguage"] == ["Python"]
    assert "pulse:language" not in repo


def test_raw_projection_strips_metadata_nested_inside_declared_properties():
    result = project_raw([{
        "@id": REPOSITORY,
        "@type": "schema:SoftwareSourceCode",
        "schema:author": [{"@id": "https://github.com/alice", "_trace": "private"}],
    }], run_id="nested")
    repo = next(node for node in result["@graph"] if node["@id"] == REPOSITORY)
    assert repo["schema:author"] == [{"@id": "https://github.com/alice"}]
