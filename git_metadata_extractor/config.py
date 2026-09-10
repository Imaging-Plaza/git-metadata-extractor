from __future__ import annotations

import os
from dataclasses import dataclass, field

from git_metadata_extractor.agents.runtime import AgentRuntime, parse_agent_runtime

TRUE_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_ENV_VALUES = {"0", "false", "f", "no", "n", "off"}
MISSING_GME_GITHUB_TOKEN_ERROR = "Missing required environment variable: GME_GITHUB_TOKEN"  # noqa: S105


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


# --------------------------------------------------------------------------
# substrate writer (provenance architecture, phase 3)
#
# These live here rather than in `api/_helpers` on purpose. The existing stage
# gates are read out of the HTTP layer via a deferred import, because
# `api/__init__ -> api/extract -> pipeline/run` is a cycle — and the direction
# of that cycle is the tell that process configuration does not belong in the
# request layer (see the layering note in `pipeline/run.py::_gate`). A new flag
# should not join that debt, so `pipeline/run.py` imports these directly.
#
# Read at call time, not at import: the tests and the corpus runs both toggle
# them with `monkeypatch.setenv`, which a module-level constant would ignore.
# --------------------------------------------------------------------------


def substrate_enabled() -> bool:
    """Read `V2_SUBSTRATE_ENABLED` (default **false**).

    Turns on the raw/substrate projection: the extracted entities grouped into
    one named graph per `pulse:ExtractionOutput` — one platform's slice of one
    run. Off by default because the layer is additive and unconsumed until the
    store-side unifier (phase 4) reads it; on, `/v2/extract` carries it as the
    `substrate` field beside `output`.
    """
    return _get_env_bool("V2_SUBSTRATE_ENABLED", default_value=False)


def substrate_validate() -> bool:
    """Read `V2_SUBSTRATE_VALIDATE` (default **true** when the substrate is on).

    The reporting half of the validation split. On by default because it costs
    one SHACL pass over a document already in memory and because a substrate
    violation is a producer bug worth seeing — the raw shapes are open, so
    anything they reject is genuinely malformed rather than merely unexpected.

    Turn it off for a bulk backfill where the SHACL pass dominates the run.
    """
    return _get_env_bool("V2_SUBSTRATE_VALIDATE", default_value=True)


def substrate_store_url() -> str | None:
    """Read `V2_SUBSTRATE_STORE_URL` (unset by default).

    The Oxigraph server root, e.g. `http://gme-oxigraph:7878`. When unset the
    substrate is still projected and returned but not written anywhere, which
    is the useful middle state: the layer can be inspected before a store
    exists to put it in.
    """
    return _get_optional_env("V2_SUBSTRATE_STORE_URL")


def substrate_store_timeout_seconds() -> float:
    """Read `V2_SUBSTRATE_STORE_TIMEOUT_SECONDS`.

    The default comes from `store.oxigraph` rather than being repeated here:
    that module owns the HTTP client, and a second copy of the number is how
    the two drift apart. `store/oxigraph.py` imports nothing from this project,
    so reaching into it from config is a leaf dependency, not a cycle.
    """
    from git_metadata_extractor.store.oxigraph import (  # noqa: PLC0415
        DEFAULT_TIMEOUT_SECONDS,
    )

    raw_value = os.getenv("V2_SUBSTRATE_STORE_TIMEOUT_SECONDS")
    if raw_value is None or not raw_value.strip():
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw_value)
    except ValueError as exc:
        message = f"Invalid float value for V2_SUBSTRATE_STORE_TIMEOUT_SECONDS: {raw_value!r}"
        raise ValueError(message) from exc


@dataclass(slots=True)
class V2Config:
    V2_AGENT_RUNTIME_DEFAULT: AgentRuntime = field(
        default_factory=lambda: parse_agent_runtime(
            os.getenv("V2_AGENT_RUNTIME_DEFAULT"),
            default=AgentRuntime.LLM,
            field_name="V2_AGENT_RUNTIME_DEFAULT",
        ),
    )
    V2_PROVIDER_CACHE_PATH: str = field(
        default_factory=lambda: os.getenv(
            "V2_PROVIDER_CACHE_PATH",
            ".cache/v2/providers.db",
        ),
    )
    V2_PROVIDER_CACHE_TTL_DAYS: int = field(
        default_factory=lambda: _get_env_int("V2_PROVIDER_CACHE_TTL_DAYS", 30),
    )
    GME_GITHUB_TOKEN: str | None = field(default_factory=lambda: _get_optional_env("GME_GITHUB_TOKEN"))

    def validate_preflight(self) -> None:
        if not self.GME_GITHUB_TOKEN:
            raise ValueError(MISSING_GME_GITHUB_TOKEN_ERROR)
