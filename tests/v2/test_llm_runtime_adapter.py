from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

import git_metadata_extractor.agents.llm.runtime as runtime_module
from git_metadata_extractor.agents.llm.runtime import (
    LLMRuntimeConfigError,
    LLMRuntimeResponseError,
    V2LLMRuntime,
)

EXPECTED_PROMPT_TOKENS = 17
EXPECTED_COMPLETION_TOKENS = 23


@dataclass
class _FakeUsage:
    input_tokens: int = 11
    output_tokens: int = 7
    requests: int = 3
    tool_calls: int = 2
    details: dict[str, int] | None = None


@dataclass
class _FakeResult:
    output: Any
    usage: Any = None


def test_llm_runtime_selects_first_valid_profile_and_returns_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_run_kwargs: dict[str, Any] = {}

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        async def run(
            self,
            prompt: str,
            model_settings: dict[str, Any] | None = None,
        ) -> _FakeResult:
            captured_run_kwargs["prompt"] = prompt
            captured_run_kwargs["model_settings"] = model_settings
            return _FakeResult(
                output={"id": "repo-id"},
                usage=_FakeUsage(
                    input_tokens=EXPECTED_PROMPT_TOKENS,
                    output_tokens=EXPECTED_COMPLETION_TOKENS,
                ),
            )

    monkeypatch.setattr(
        runtime_module,
        "load_model_config",
        lambda _analysis_type: [
            {"provider": "openai", "model": "broken-model", "valid": False},
            {
                "provider": "openai",
                "model": "gpt-test",
                "valid": True,
                "timeout": 42.0,
            },
        ],
    )
    monkeypatch.setattr(
        runtime_module,
        "validate_config",
        lambda config: bool(config.get("valid")),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_pydantic_ai_model",
        lambda _config: object(),
    )
    monkeypatch.setattr(
        runtime_module,
        "get_model_parameters",
        lambda _config: {"temperature": 0.1},
    )
    monkeypatch.setattr(runtime_module, "Agent", _FakeAgent)
    monkeypatch.setenv("OPENAI_API_KEY", "test-value")

    runtime = V2LLMRuntime()
    result = asyncio.run(
        runtime.run_json_prompt(
            system_prompt="system",
            user_prompt="user prompt",
        ),
    )

    assert result.payload == {"id": "repo-id"}
    assert result.model == "gpt-test"
    assert result.provider == "openai"
    assert result.tokens_prompt == EXPECTED_PROMPT_TOKENS
    assert result.tokens_completion == EXPECTED_COMPLETION_TOKENS
    assert result.requests == 3
    assert result.tool_calls == 2
    assert captured_run_kwargs["model_settings"] == {"temperature": 0.1, "timeout": 42.0}


def test_llm_runtime_reports_missing_provider_credentials_by_env_var_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "load_model_config",
        lambda _analysis_type: [
            {
                "provider": "openrouter",
                "model": "google/gemini-test",
                "max_retries": 2,
            },
        ],
    )
    monkeypatch.setattr(runtime_module, "validate_config", lambda _config: True)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    runtime = V2LLMRuntime()
    with pytest.raises(
        LLMRuntimeConfigError,
        match="OPENROUTER_API_KEY",
    ):
        asyncio.run(
            runtime.run_json_prompt(
                system_prompt="system",
                user_prompt="user prompt",
            ),
        )


def test_llm_runtime_rejects_non_json_string_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        async def run(
            self,
            prompt: str,
            model_settings: dict[str, Any] | None = None,
        ) -> _FakeResult:
            del prompt, model_settings
            return _FakeResult(output="not-json")

    monkeypatch.setattr(
        runtime_module,
        "load_model_config",
        lambda _analysis_type: [
            {"provider": "openai", "model": "gpt-test", "valid": True},
        ],
    )
    monkeypatch.setattr(runtime_module, "validate_config", lambda _config: True)
    monkeypatch.setattr(
        runtime_module,
        "create_pydantic_ai_model",
        lambda _config: object(),
    )
    monkeypatch.setattr(runtime_module, "get_model_parameters", lambda _config: {})
    monkeypatch.setattr(runtime_module, "Agent", _FakeAgent)
    monkeypatch.setenv("OPENAI_API_KEY", "test-value")

    runtime = V2LLMRuntime()
    with pytest.raises(LLMRuntimeResponseError, match="not valid JSON"):
        asyncio.run(
            runtime.run_json_prompt(
                system_prompt="system",
                user_prompt="user prompt",
            ),
        )


# --- per-request model override (#1b) ---------------------------------------


def _base_config() -> dict[str, Any]:
    return {"provider": "openai", "model": "gpt-4o", "api_key_env": "OPENAI_API_KEY"}


def test_request_override_ignored_when_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("V2_ALLOW_REQUEST_MODEL_OVERRIDE", raising=False)
    token = runtime_module.set_request_model_override(
        {"model": "Qwen/Qwen3", "base_url": "https://rcp/v1", "api_key_env": "RCP_TOKEN"},
    )
    try:
        assert runtime_module._apply_request_override(_base_config()) == _base_config()
    finally:
        runtime_module.reset_request_model_override(token)


def test_request_override_applied_when_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_ALLOW_REQUEST_MODEL_OVERRIDE", "true")
    token = runtime_module.set_request_model_override(
        {
            "provider": "openai-compatible",
            "model": "Qwen/Qwen3",
            "base_url": "https://rcp/v1",
            "api_key_env": "RCP_TOKEN",
        },
    )
    try:
        merged = runtime_module._apply_request_override(_base_config())
    finally:
        runtime_module.reset_request_model_override(token)
    assert merged["provider"] == "openai-compatible"
    assert merged["model"] == "Qwen/Qwen3"
    assert merged["base_url"] == "https://rcp/v1"
    assert merged["api_key_env"] == "RCP_TOKEN"


def test_request_override_drops_unknown_and_empty_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V2_ALLOW_REQUEST_MODEL_OVERRIDE", "on")
    token = runtime_module.set_request_model_override(
        {"model": "m", "evil": "x", "base_url": ""},
    )
    try:
        merged = runtime_module._apply_request_override(_base_config())
    finally:
        runtime_module.reset_request_model_override(token)
    assert merged["model"] == "m"
    assert "evil" not in merged  # unknown key dropped
    assert "base_url" not in merged  # empty override value dropped, base had none
