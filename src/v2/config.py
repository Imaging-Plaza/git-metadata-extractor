from __future__ import annotations

import os
from dataclasses import dataclass, field

TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_ENV_VALUES = {"0", "false", "f", "no", "n", "off"}
MISSING_GITHUB_TOKEN_ERROR = "Missing required environment variable: GITHUB_TOKEN"  # noqa: S105


def _get_env_bool(name: str, *, default_value: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default_value

    normalized_value = raw_value.strip().lower()
    if normalized_value in TRUE_ENV_VALUES:
        return True
    if normalized_value in FALSE_ENV_VALUES:
        return False
    message = f"Invalid boolean value for {name}: {raw_value!r}"
    raise ValueError(message)


def _get_env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        return int(raw_value)
    except ValueError as exc:
        message = f"Invalid integer value for {name}: {raw_value!r}"
        raise ValueError(message) from exc


def _get_optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped_value = value.strip()
    return stripped_value or None


@dataclass(slots=True)
class V2Config:
    V2_GRAPH_DB_PATH: str = field(
        default_factory=lambda: os.getenv("V2_GRAPH_DB_PATH", "data/v2_graph.db"),
    )
    V2_INTERMEDIATE_HISTORY_LIMIT: int = field(
        default_factory=lambda: _get_env_int("V2_INTERMEDIATE_HISTORY_LIMIT", 5),
    )
    V2_ENABLE_LOGFIRE: bool = field(
        default_factory=lambda: _get_env_bool("V2_ENABLE_LOGFIRE", default_value=True),
    )
    V2_ALLOW_SYNTHETIC_FALLBACKS: bool = field(
        default_factory=lambda: _get_env_bool(
            "V2_ALLOW_SYNTHETIC_FALLBACKS",
            default_value=False,
        ),
    )
    LOGFIRE_TOKEN: str | None = field(default_factory=lambda: _get_optional_env("LOGFIRE_TOKEN"))
    GITHUB_TOKEN: str | None = field(default_factory=lambda: _get_optional_env("GITHUB_TOKEN"))

    def validate_preflight(self) -> None:
        if not self.GITHUB_TOKEN:
            raise ValueError(MISSING_GITHUB_TOKEN_ERROR)
