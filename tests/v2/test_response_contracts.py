from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.v2.api_models.contracts import (
    V2ExtractResponse,
    V2JSONLDOutput,
    V2JSONOutputEnvelope,
    V2Stats,
)


def _sample_stats() -> V2Stats:
    return V2Stats(
        entities_count=1,
        triples_count=2,
        run_id="run-test",
        duration_ms=3,
        stages_completed=["extract"],
    )


def test_extract_response_can_be_instantiated_with_required_fields() -> None:
    response = V2ExtractResponse(
        source_url="https://github.com/owner/repo",
        detected_type="repository",
        output_format="jsonld",
        output={"@context": {}, "@graph": []},
        stats=_sample_stats(),
    )

    assert response.source_url == "https://github.com/owner/repo"
    assert response.detected_type == "repository"


def test_extract_response_serializes_to_expected_contract_shape() -> None:
    response = V2ExtractResponse(
        source_url="https://github.com/owner/repo",
        detected_type="repository",
        output_format="json",
        output={
            "root_entity": {"id": "https://github.com/owner/repo"},
            "related_entities": [],
            "excluded_entities": [],
            "entities_by_type": {
                "repositories": [{"id": "https://github.com/owner/repo"}],
                "persons": [],
                "organizations": [],
                "articles": [],
                "memberships": [],
                "contributions": [],
            },
        },
        stats=_sample_stats(),
    )
    payload = response.model_dump(mode="json")

    assert payload["source_url"] == "https://github.com/owner/repo"
    assert payload["detected_type"] == "repository"
    assert payload["output_format"] == "json"
    assert payload["output"]["root_entity"]["id"] == "https://github.com/owner/repo"
    assert payload["output"]["related_entities"] == []
    assert payload["output"]["excluded_entities"] == []
    assert payload["output"]["entities_by_type"]["repositories"]
    assert payload["warnings"] == []
    assert payload["stats"]["entities_count"] == 1


def test_extract_response_output_format_rejects_unsupported_values() -> None:
    with pytest.raises(ValidationError, match="output_format"):
        V2ExtractResponse(
            source_url="https://github.com/owner/repo",
            detected_type="repository",
            output_format="xml",
            output={},
            stats=_sample_stats(),
        )


def test_extract_response_warnings_default_to_empty_list() -> None:
    response = V2ExtractResponse(
        source_url="https://github.com/owner/repo",
        detected_type="repository",
        output_format="jsonld",
        output={"@context": {}, "@graph": []},
        stats=_sample_stats(),
    )

    assert response.warnings == []


def test_extract_response_rejects_deprecated_stage_keyed_json_entities_shape() -> None:
    with pytest.raises(ValidationError, match="output"):
        V2ExtractResponse(
            source_url="https://github.com/owner/repo",
            detected_type="repository",
            output_format="json",
            output={"entities": {"repo_agent": {"id": "https://github.com/owner/repo"}}},
            stats=_sample_stats(),
        )


def test_extract_response_output_enforces_jsonld_and_json_contract_pairing() -> None:
    with pytest.raises(ValidationError, match="jsonld"):
        V2ExtractResponse(
            source_url="https://github.com/owner/repo",
            detected_type="repository",
            output_format="jsonld",
            output={
                "root_entity": {"id": "https://github.com/owner/repo"},
                "related_entities": [],
                "excluded_entities": [],
                "entities_by_type": {},
            },
            stats=_sample_stats(),
        )

    with pytest.raises(ValidationError, match="json envelope"):
        V2ExtractResponse(
            source_url="https://github.com/owner/repo",
            detected_type="repository",
            output_format="json",
            output={"@context": {}, "@graph": []},
            stats=_sample_stats(),
        )


def test_output_contract_models_support_alias_serialization() -> None:
    jsonld = V2JSONLDOutput(
        **{
            "@context": {"schema": "http://schema.org/"},
            "@graph": [{"@id": "https://example.org/node", "@type": "schema:Thing"}],
        },
    )
    assert jsonld.model_dump(mode="json", by_alias=True)["@graph"]

    envelope = V2JSONOutputEnvelope(
        root_entity=None,
        related_entities=[],
        excluded_entities=[],
        entities_by_type={
            "repositories": [],
            "persons": [],
            "organizations": [],
            "articles": [],
            "memberships": [],
            "contributions": [],
        },
    )
    assert envelope.root_entity is None


