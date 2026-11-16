from __future__ import annotations

from git_metadata_extractor.api_models.errors import V2ErrorResponse, V2ErrorType, V2FieldError


def test_error_response_serializes_correctly_for_unsupported_url() -> None:
    payload = V2ErrorResponse(
        error_type="unsupported_url",
        detail="repository subresource URLs not supported",
    ).model_dump(mode="json", exclude_none=True)

    assert payload == {
        "error_type": "unsupported_url",
        "detail": "repository subresource URLs not supported",
    }


def test_error_type_enum_contains_all_planned_values() -> None:
    assert {error_type.value for error_type in V2ErrorType} == {
        "unsupported_url",
        "validation_error",
        "provider_error",
        "pipeline_error",
        "not_found",
    }


def test_field_error_captures_field_message_and_value() -> None:
    field_error = V2FieldError(
        field="output_format",
        message="unexpected value",
        value="xml",
    )

    assert field_error.field == "output_format"
    assert field_error.message == "unexpected value"
    assert field_error.value == "xml"
