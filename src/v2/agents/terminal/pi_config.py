"""Per-run pi configuration bootstrap for the v2 terminal-agent PoC.

Pi looks for `models.json` and `settings.json` under `~/.pi/agent/` by
default, overridable via the `PI_CODING_AGENT_DIR` environment variable.
We exploit that override to write a fresh config into each run's tempdir
and point pi at it — zero pollution of the user's global pi config, and
every run is fully self-contained and reproducible.

The provider definition uses pi's value-resolution: `apiKey: "RCP_TOKEN"`
is read as an environment-variable name at request time, so the actual
token never lands on disk.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class PiProviderSpec:
    """Minimal spec to register an OpenAI-compatible RCP provider with pi."""

    name: str
    base_url: str
    api_key_env: str
    models: list[str]
    provider_id: str = "rcp"


def write_pi_config(
    agent_dir: Path,
    *,
    provider: PiProviderSpec,
    default_model: str,
) -> None:
    """Materialise `models.json` + `settings.json` under `agent_dir`.

    `agent_dir` must be the directory pi will be pointed at via
    `PI_CODING_AGENT_DIR` — i.e. the equivalent of `~/.pi/agent`.

    `default_model` becomes pi's default so the runner can omit
    `--provider`/`--model` flags on the command line.
    """
    agent_dir.mkdir(parents=True, exist_ok=True)
    _write_models_json(agent_dir, provider)
    _write_settings_json(agent_dir, provider_id=provider.provider_id, default_model=default_model)


def _write_models_json(agent_dir: Path, provider: PiProviderSpec) -> None:
    payload = {
        "providers": {
            provider.provider_id: {
                "name": provider.name,
                "baseUrl": provider.base_url,
                "api": "openai-completions",
                # Pi resolves a bare-name string as an env-var lookup at
                # request time. Token never lands on disk.
                "apiKey": provider.api_key_env,
                # OpenAI-compatible: send `Authorization: Bearer <token>`.
                "authHeader": True,
                # vLLM/SGLang-style backends typically reject the
                # `developer` role and `reasoning_effort` knob that pi
                # otherwise emits for reasoning-capable models. Disable
                # both at the provider level — RCP exposes plain chat.
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [{"id": model_id, "name": model_id} for model_id in provider.models],
            },
        },
    }
    path = agent_dir / "models.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.debug("wrote pi models.json: %s", path)


def _write_settings_json(agent_dir: Path, *, provider_id: str, default_model: str) -> None:
    payload = {
        "defaultProvider": provider_id,
        "defaultModel": default_model,
        # Telemetry off and quiet startup are the right defaults for a
        # batch PoC harness — no install pings, no header noise in the
        # captured transcript.
        "enableInstallTelemetry": False,
        "quietStartup": True,
    }
    path = agent_dir / "settings.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.debug("wrote pi settings.json: %s", path)


__all__ = ["PiProviderSpec", "write_pi_config"]
