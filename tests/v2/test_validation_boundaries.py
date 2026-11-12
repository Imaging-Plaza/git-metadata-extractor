"""Validation must agree across generation, public output and persistence."""

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator
from pydantic import ValidationError

from git_metadata_extractor.agents.models import validate_permissive
from git_metadata_extractor.pipeline.stages.jsonld_build import build_jsonld_output
from git_metadata_extractor.pipeline.stages.models import AssembledOutput
from git_metadata_extractor.pipeline.stages.output_assembly import build_json_output
from git_metadata_extractor.schema.generated.canonical import ArticleModel
from git_metadata_extractor.unify.runner import write_canonical
from git_metadata_extractor.validation import layers
from git_metadata_extractor.validation.schema_validation import StrictSchemaValidator


def person():
    path = Path(__file__).parent / "fixtures/schema/strict/pulse_PersonShape.json"
    return json.loads(path.read_text())[0]


def test_strict_validation_enforces_declared_url_format():
    payload = person()
    payload["schema:url"] = "not a uri"
    result = StrictSchemaValidator().validate("person", payload)
    assert not result.is_valid
    assert any(error["constraint"] == "format" for error in result.errors)


def test_nested_metadata_is_ignored_by_validation_without_mutation():
    payload = person()
    payload["identifiers"]["_source"] = "private"
    assert StrictSchemaValidator().validate("person", payload).is_valid
    assert payload["identifiers"]["_source"] == "private"


def test_permissive_validation_removes_nested_extras_using_nested_schema():
    payload = person()
    payload["identifiers"].update(unexpected="extra", _source="internal")
    validated, warnings = validate_permissive(payload, schema_name="person")
    assert "unexpected" not in validated["identifiers"]
    assert validated["identifiers"]["uuid"] == payload["identifiers"]["uuid"]
    assert validated["identifiers"]["_source"] == "internal"
    assert not any(
        "Removed unexpected fields: pulse:orcid" in warning for warning in warnings
    )


@pytest.mark.parametrize("output", ["json", "jsonld"])
def test_excluded_output_strips_nested_internal_metadata(output):
    payload = person()
    payload["identifiers"]["_source"] = "private"
    payload["nested"] = [{"_secret": "hidden", "public": "visible"}]
    assembled = AssembledOutput(
        root_entity=None, excluded_entities=[{"entity": payload, "_debug": "private"}],
    )
    result = (
        build_json_output(assembled)
        if output == "json"
        else build_jsonld_output(assembled=assembled, jsonld_context={})
    )
    assert "_source" not in json.dumps(result)
    assert "_secret" not in json.dumps(result)
    assert "_debug" not in json.dumps(result)
    assert "visible" in json.dumps(result)
    assert (
        assembled.excluded_entities[0]["entity"]["identifiers"]["_source"] == "private"
    )


def test_generated_required_lists_reject_empty_values():
    with pytest.raises(ValidationError) as caught:
        ArticleModel.model_validate(
            {
                "pulse:doi": [],
                "pulse:hasDeposit": [],
                "schema:author": [],
                "schema:name": [],
            },
        )
    assert {
        error["loc"] for error in caught.value.errors() if error["type"] == "too_short"
    } == {
        ("pulse:doi",),
        ("pulse:hasDeposit",),
        ("schema:author",),
        ("schema:name",),
    }


def test_format_dependency_cannot_silently_disable_uri_validation(monkeypatch):
    monkeypatch.delitem(Draft7Validator.FORMAT_CHECKER.checkers, "uri")
    with pytest.raises(RuntimeError, match="URI validation is unavailable"):
        StrictSchemaValidator().validate("person", person())


@pytest.mark.parametrize("reason", ["missing shapes", "missing runtime"])
def test_canonical_writer_never_mutates_store_when_validation_is_skipped(
    monkeypatch, reason,
):
    monkeypatch.setattr(
        layers,
        "validate_layer",
        lambda *_args, **_kwargs: layers.LayerValidationResult(
            layer=layers.Layer.CANONICAL,
            conforms=True,
            skipped=True,
            reason=reason,
        ),
    )
    calls = []

    class Store:
        async def update(self, query):
            calls.append(query)

        async def load_nquads(self, data):
            calls.append(data)

    with pytest.raises(RuntimeError, match="not published"):
        asyncio.run(write_canonical(Store(), {"@context": {}, "@graph": []}))
    assert not calls
