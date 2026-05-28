from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class V2ErrorType(str, Enum):
    UNSUPPORTED_URL = "unsupported_url"
    VALIDATION_ERROR = "validation_error"
    PROVIDER_ERROR = "provider_error"
    PIPELINE_ERROR = "pipeline_error"
    NOT_FOUND = "not_found"


class V2FieldError(BaseModel):
    field: str
    message: str
    value: Any | None = None


class V2ErrorResponse(BaseModel):
    error_type: V2ErrorType
    detail: str
    source_url: str | None = None
    detected_path_kind: str | None = None
    errors: list[V2FieldError] | None = None
