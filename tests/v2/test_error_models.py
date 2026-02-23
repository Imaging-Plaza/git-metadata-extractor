from __future__ import annotations

from fastapi import HTTPException

from src.v2.models.errors import V2ErrorResponse, V2ErrorType, V2FieldError

HTTP_UNPROCESSABLE_ENTITY = 422


def test_error_response_serializes_correctly_for_unsupported_url() -> None:
    payload = V2ErrorResponse(
        error_type="unsupported_url",
        detail="repository subresource URLs not supported",
    ).model_dump(mode="json", exclude_none=True)

    assert payload == {
        "error_type": "unsupported_url",
        "detail": "repository subresource URLs not supported",
    }


def test_error_response_shape_works_with_fastapi_http_exception() -> None:
    response_model = V2ErrorResponse(
        error_type=V2ErrorType.VALIDATION_ERROR,
        detail="Schema validation failed",
        source_url="https://github.com/owner/repo",
    )
    exception = HTTPException(
        status_code=HTTP_UNPROCESSABLE_ENTITY,
        detail=response_model.model_dump(mode="json"),
    )

    assert exception.status_code == HTTP_UNPROCESSABLE_ENTITY
    assert exception.detail["error_type"] == "validation_error"
    assert exception.detail["source_url"] == "https://github.com/owner/repo"


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


def test_error_models_are_importable() -> None:
    assert V2ErrorResponse.__name__ == "V2ErrorResponse"
    assert V2ErrorType.__name__ == "V2ErrorType"
    assert V2FieldError.__name__ == "V2FieldError"
