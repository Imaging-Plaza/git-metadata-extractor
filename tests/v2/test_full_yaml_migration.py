from __future__ import annotations

# Fingerprint tests deliberately reset process-local configuration caches.
# ruff: noqa: SLF001
import pytest
from pydantic import ValidationError

from git_metadata_extractor.mapping import (
    engine,
    load_mapping,
    load_policy,
    map_fields,
    mapping_manifest,
    policies,
)
from git_metadata_extractor.mapping.engine import parse_mapping
from git_metadata_extractor.mapping.models import FieldRule
from git_metadata_extractor.mapping.policies import (
    ExtractionPolicy,
    MergeRules,
    OntologyPolicy,
)


def document(fields):
    return f"""
schema_version: 1
name: agent.person
version: '1'
source_contract: prepared.person
target_contract: agent.person
fields:
{fields}
"""


def test_constants_nested_identifiers_and_empty_facts():
    spec = parse_mapping(
        document("""
  type: {value: 'schema:Person'}
  identifiers.uuid: {sources: [uuid]}
  org:hasMembership: {sources: [memberships], empty: preserve}
"""),
    )
    assert spec.fields["type"].value == "schema:Person"
    assert spec.fields["org:hasMembership"].empty == "preserve"


def test_constant_and_source_are_mutually_exclusive():
    with pytest.raises(ValidationError, match="exactly one"):
        parse_mapping(document("  type: {value: 'schema:Person', sources: [kind]}"))


def test_unknown_nested_target_is_rejected():
    with pytest.raises(ValueError, match="undeclared"):
        parse_mapping(document("  identifiers.invented: {sources: [name]}"))


def test_parent_and_child_targets_cannot_collide():
    with pytest.raises(ValueError, match="collid"):
        parse_mapping(
            document("""
  identifiers: {sources: [ids]}
  identifiers.uuid: {sources: [uuid]}
"""),
        )


def test_enum_mapping_requires_explicit_values():
    with pytest.raises(ValidationError, match="enum"):
        parse_mapping(document("  idSource: {sources: [source], transform: enum}"))


@pytest.mark.parametrize(
    "rule",
    [
        {"sources": ["name"]},
        {"value": None},
        {"value": []},
        {"value": "schema:Person"},
    ],
)
def test_field_rule_serialization_roundtrip(rule):
    parsed = FieldRule.model_validate(rule)
    assert FieldRule.model_validate(parsed.model_dump()) == parsed


def test_nested_mapping_execution_preserves_empty_facts_without_inventing_nulls(
    monkeypatch,
):
    spec = parse_mapping(
        document("""
  type: {value: 'schema:Person'}
  identifiers.uuid: {sources: [uuid]}
  org:hasMembership: {sources: [memberships], empty: preserve}
  pulse:owns: {sources: [absent], empty: preserve}
  schema:name: {sources: [absent], missing: 'null'}
"""),
    )
    monkeypatch.setattr(engine, "_load", lambda _: spec)
    result = map_fields("agent.person", {"uuid": "test-uuid", "memberships": []})
    assert result == {
        "type": "schema:Person",
        "identifiers": {"uuid": "test-uuid"},
        "org:hasMembership": [],
        "schema:name": None,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PUBLIC", "pulse:PublicVisibility"),
        ("private", "pulse:PrivateVisibility"),
        ("unknown", None),
    ],
)
def test_repository_visibility_enum(value, expected):
    result = map_fields("raw.repository", {"_visibility": value})
    assert result.get("pulse:visibility") == expected


def test_full_inventory_includes_agents_source_adapters_and_policies():
    manifest = mapping_manifest()
    names = {entry["name"] for entry in manifest["mappings"]}
    assert {
        f"agent.{kind}"
        for kind in (
            "repository",
            "person",
            "organization",
            "article",
            "membership",
            "contribution",
        )
    } <= names
    assert {
        "github.person",
        "github.organization",
        "ror.organization",
        "orcid.person",
        "infoscience.person",
        "infoscience.organization",
        "canonical.deposit",
        "raw.deposit",
    } <= names
    assert {entry["name"] for entry in manifest["policies"]} == set(policies.POLICIES)
    for name in names:
        spec = load_mapping(name)
        assert type(spec).model_validate(spec.model_dump()) == spec


@pytest.mark.parametrize(
    ("name", "model", "field", "invalid"),
    [
        (
            "policies.ontology",
            OntologyPolicy,
            "source_by_property",
            {"schema:invented": "pulse:GitHub"},
        ),
        ("policies.ontology", OntologyPolicy, "source_url_fields", ["schema:invented"]),
        (
            "policies.extraction",
            ExtractionPolicy,
            "repository_default",
            "pulse:Invented",
        ),
        (
            "policies.merge",
            MergeRules,
            "iri_templates",
            {"pulse:doi": "https://doi.org/{value}"},
        ),
    ],
)
def test_invalid_policy_edits_fail_validation(name, model, field, invalid):
    data = load_policy(name).model_dump()
    data[field] = invalid
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_merge_requires_uncapped_coverage_and_disallows_capped_union():
    data = load_policy("policies.merge").model_dump()
    del data["dispositions"]["schema:name"]
    with pytest.raises(ValidationError, match="unclassified"):
        MergeRules.model_validate(data)
    data = load_policy("policies.merge").model_dump()
    data["dispositions"]["pulse:repositoryStars"] = "union"
    with pytest.raises(ValidationError, match="capped"):
        MergeRules.model_validate(data)


@pytest.mark.parametrize("field", ["match_keys", "id_priority"])
def test_resolver_rejects_cross_entity_fields(field):
    data = load_policy("policies.merge").model_dump()
    data["resolvers"]["schema:Person"][field].append("pulse:doi")
    with pytest.raises(ValidationError, match="schema:Person"):
        MergeRules.model_validate(data)


def test_resolver_requires_strongest_identity_match_first():
    data = load_policy("policies.merge").model_dump()
    data["resolvers"]["schema:Person"]["match_keys"].reverse()
    with pytest.raises(ValidationError, match="matched first"):
        MergeRules.model_validate(data)


def test_transform_empty_result_uses_missing_policy():
    assert map_fields("github.repository", {"topics": ["", 3]})["_keywords"] is None


def test_policy_edit_changes_fingerprint_and_loaded_rules(tmp_path, monkeypatch):
    source = engine.CONFIG_ROOT / "policies/extraction.yaml"
    path = tmp_path / "policies/extraction.yaml"
    path.parent.mkdir()
    path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(engine, "CONFIG_ROOT", tmp_path)
    policies._load_policy.cache_clear()
    engine._manifest.cache_clear()
    try:
        before = mapping_manifest()["fingerprint"]
        path.write_text(
            path.read_text(encoding="utf-8").replace("version: '1'", "version: '2'"),
            encoding="utf-8",
        )
        policies._load_policy.cache_clear()
        engine._manifest.cache_clear()
        assert load_policy("policies.extraction").version == "2"
        assert mapping_manifest()["fingerprint"] != before
    finally:
        policies._load_policy.cache_clear()
        engine._manifest.cache_clear()
