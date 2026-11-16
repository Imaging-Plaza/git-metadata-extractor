"""Late hybrid ROR facts survive projection without renaming referenced nodes."""

import pytest

from git_metadata_extractor.pipeline.stages.canonical_projection import (
    project_canonical,
)
from git_metadata_extractor.pipeline.stages.jsonld_build import build_projection_input
from git_metadata_extractor.pipeline.stages.models import AssembledOutput
from git_metadata_extractor.pipeline.stages.raw_projection import project_raw
from git_metadata_extractor.pipeline.stages.refine_with_llm import (
    OrgResolverPatch,
    _apply_org_resolver_patch,
)
from git_metadata_extractor.pipeline.stages.substrate import build_substrate
from git_metadata_extractor.schema import load_generated_context, load_jsonld_context
from git_metadata_extractor.validation.layers import Layer, validate_layer
from git_metadata_extractor.validation.schema_validation import StrictSchemaValidator

UUID = "ea4d9658-25e9-4bc4-b10d-783b4a060a93"
ROR = "https://ror.org/036jqmy94"


def test_hybrid_ror_patch_survives_strict_jsonld_and_both_projections():
    # Synthetic replay of the producer contract, not a claimed live LLM patch.
    org = {
        "id": UUID, "type": "org:Organization", "shacl": "pulse:OrganizationShape",
        "idSource": "uuid", "schema:name": "Univeristy of Iowa",
        "identifiers": {"uuid": UUID},
    }
    assert _apply_org_resolver_patch(org, OrgResolverPatch(
        pulse_ror=ROR, schema_name_canonical="University of Iowa", confidence=0.95,
    ))
    validation = StrictSchemaValidator().validate("organization", org)
    assert validation.is_valid, validation.errors
    membership = {
        "id": "urn:test:membership", "type": "org:Membership",
        "org:organization": UUID,
    }
    flat = build_projection_input(
        assembled=AssembledOutput(root_entity=org, related_entities=[membership]),
        jsonld_context=load_jsonld_context(),
    )
    canonical = project_canonical(flat)
    canonical["@context"] = load_generated_context()
    projected_org = next(n for n in canonical["@graph"] if n["@type"] == "org:Organization")
    assert projected_org["@id"] == f"urn:pulse:{UUID}"
    assert projected_org["pulse:ror"] == [ROR]
    projected_membership = next(n for n in canonical["@graph"] if n["@type"] == "org:Membership")
    assert projected_membership["org:organization"] == {"@id": f"urn:pulse:{UUID}"}
    result = validate_layer(canonical, Layer.CANONICAL)
    assert not result.skipped
    assert result.conforms, result.violations

    substrate = build_substrate(
        flat, run_id="ror-retention", context=load_generated_context(),
        run_nodes=[{"@id": "urn:pulse:run:ror-retention", "@type": "pulse:ExtractionRun"}],
    )
    ror_graph = next(g for g in substrate["@graph"] if g["@id"] == "urn:pulse:output:ror-retention:ror")
    raw_org = next(n for n in ror_graph["@graph"] if n["@id"] == f"urn:pulse:{UUID}")
    assert raw_org["pulse:ror"] == ROR
    assert raw_org["pulse:partOfRun"] == {"@id": ror_graph["@id"]}
    assert not any(
        n["@id"] == f"urn:pulse:{UUID}"
        for g in substrate["@graph"] if g["@id"].endswith("#meta") for n in g["@graph"]
    )
    result = validate_layer(substrate, Layer.SUBSTRATE, quads=True)
    assert not result.skipped
    assert result.conforms, result.violations


@pytest.mark.parametrize("identifier", ["University of Iowa", "036jqmy94", "https://example.org/036jqmy94", "https://ror.org/bad", None])
def test_projection_does_not_invent_ror_from_unvalidated_identifier(identifier):
    node = {"@id": f"urn:pulse:{UUID}", "@type": "org:Organization", "schema:name": "Unknown", "schema:identifier": identifier}
    for document in (project_canonical([node]), project_raw([node], run_id="invalid")):
        org = next(n for n in document["@graph"] if n["@type"] == "org:Organization")
        assert "pulse:ror" not in org
        assert "pulse:partOfRun" not in org


def test_late_ror_preserves_github_anchor_and_splits_ror_fact():
    node = {
        "@id": f"urn:pulse:{UUID}", "@type": "org:Organization", "schema:name": "Lab",
        "schema:identifier": ROR, "pulse:githubOrganizationHandle": "https://github.com/example",
    }
    raw = project_raw([node], run_id="split")
    copies = [n for n in raw["@graph"] if n["@id"] == f"urn:pulse:{UUID}"]
    github = next(n for n in copies if n["pulse:partOfRun"]["@id"].endswith(":github"))
    ror = next(n for n in copies if n["pulse:partOfRun"]["@id"].endswith(":ror"))
    assert github["schema:name"] == "Lab"
    assert "pulse:ror" not in github
    assert ror["pulse:ror"] == ROR
