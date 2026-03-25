from __future__ import annotations

import inspect
import json
import logging
import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from pydantic_ai import Agent

from src.llm.model_config import (
    create_pydantic_ai_model,
    get_model_parameters,
    load_model_config,
    validate_config,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMRuntimeResult:
    payload: dict[str, Any]
    model: str
    provider: str
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    requests: int | None = None
    tool_calls: int | None = None


class LLMRuntimeError(RuntimeError):
    """Base error for v2 LLM runtime failures."""


class LLMRuntimeConfigError(LLMRuntimeError):
    """Raised when runtime model configuration is invalid or incomplete."""


class LLMRuntimeResponseError(LLMRuntimeError):
    """Raised when model output is not a valid JSON object payload."""


def _required_env_vars_for_config(config: dict[str, Any]) -> list[str]:
    """Return env var names required by a provider config."""

    provider = str(config.get("provider", "")).strip().lower()
    if provider == "openai":
        return ["OPENAI_API_KEY"]
    if provider == "openrouter":
        return ["OPENROUTER_API_KEY"]
    if provider == "openai-compatible":
        api_key_env = config.get("api_key_env")
        if isinstance(api_key_env, str) and api_key_env.strip():
            return [api_key_env.strip()]
        return ["OPENAI_API_KEY"]
    return []


def _extract_usage_tokens(usage: Any) -> tuple[int | None, int | None]:
    """Extract prompt/completion token counts from provider usage payloads."""

    prompt_tokens = getattr(usage, "input_tokens", None)
    completion_tokens = getattr(usage, "output_tokens", None)

    if (prompt_tokens in (None, 0)) and (completion_tokens in (None, 0)):
        details = getattr(usage, "details", None)
        if isinstance(details, dict):
            prompt_tokens = details.get("input_tokens")
            completion_tokens = details.get("output_tokens")

    normalized_prompt = (
        int(prompt_tokens) if isinstance(prompt_tokens, int) and prompt_tokens >= 0 else None
    )
    normalized_completion = (
        int(completion_tokens)
        if isinstance(completion_tokens, int) and completion_tokens >= 0
        else None
    )
    return normalized_prompt, normalized_completion


def _extract_usage_counts(usage: Any) -> tuple[int | None, int | None]:
    """Extract request/tool-call counts from usage payloads."""

    requests = getattr(usage, "requests", None)
    tool_calls = getattr(usage, "tool_calls", None)
    normalized_requests = int(requests) if isinstance(requests, int) and requests >= 0 else None
    normalized_tool_calls = (
        int(tool_calls) if isinstance(tool_calls, int) and tool_calls >= 0 else None
    )
    return normalized_requests, normalized_tool_calls


def _coerce_output_payload(output: Any) -> dict[str, Any]:
    """Convert model output into a JSON-object dictionary.

    Pydantic model instances are serialised with ``by_alias=True`` so that
    ontology field names (e.g. ``schema:name``) are preserved in the result.
    """

    if hasattr(output, "model_dump"):
        output = output.model_dump(by_alias=True, mode="json")

    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError as exc:
            message = "LLM output is not valid JSON"
            raise LLMRuntimeResponseError(message) from exc

    if not isinstance(output, dict):
        message = f"LLM output must be a JSON object, got {type(output).__name__}"
        raise LLMRuntimeResponseError(message)

    return output


class V2LLMRuntime:
    def __init__(self, *, analysis_type: str = "run_llm_analysis") -> None:
        self._analysis_type = analysis_type

    def _resolve_model_config(self) -> dict[str, Any]:
        """Pick the first valid config profile for the runtime analysis type."""

        configs = load_model_config(self._analysis_type)
        if not isinstance(configs, list) or not configs:
            message = (
                "No model configuration available for analysis type "
                f"'{self._analysis_type}'"
            )
            raise LLMRuntimeConfigError(message)

        for config in configs:
            if isinstance(config, dict) and validate_config(config):
                # Surface missing credentials by env var name only.
                missing_env_vars = [
                    env_name
                    for env_name in _required_env_vars_for_config(config)
                    if not os.getenv(env_name)
                ]
                if missing_env_vars:
                    env_list = ", ".join(sorted(set(missing_env_vars)))
                    message = (
                        "Missing required environment variable(s) for LLM runtime: "
                        f"{env_list}"
                    )
                    raise LLMRuntimeConfigError(message)
                return dict(config)

        message = f"No valid model configuration found for analysis type '{self._analysis_type}'"
        raise LLMRuntimeConfigError(message)

    async def run_json_prompt(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_type: Any = None,
        tools: list[Any] | None = None,
    ) -> LLMRuntimeResult:
        """Execute a prompt and return a structured JSON payload plus metadata.

        Args:
            system_prompt: System-level instruction for the LLM.
            user_prompt: User-level prompt containing the context to process.
            output_type: Pydantic model class to use as the structured output
                schema. Defaults to ``dict[str, Any]`` when not provided.
                Passing a Pydantic model class constrains the LLM to produce
                output that conforms to its schema (first validation pass).
        """

        resolved_output_type: Any = output_type if output_type is not None else dict[str, Any]
        config = self._resolve_model_config()
        model_name = str(config.get("model", "unknown"))
        provider_name = str(config.get("provider", "unknown"))
        run_started_at = perf_counter()

        try:
            model = create_pydantic_ai_model(config)
            retries = config.get("max_retries")
            retry_count = retries if isinstance(retries, int) and retries > 0 else 1
            agent = Agent(
                model=model,
                output_type=resolved_output_type,
                system_prompt=system_prompt,
                retries=retry_count,
                tools=tools or [],
            )
            run_kwargs: dict[str, Any] = {}
            run_parameters = inspect.signature(agent.run).parameters
            model_parameters = dict(get_model_parameters(config))
            timeout = config.get("timeout")
            if isinstance(timeout, (int, float)) and timeout > 0:
                model_parameters["timeout"] = float(timeout)
            # Pass model tuning knobs only when the backend supports the kwarg.
            if model_parameters and "model_settings" in run_parameters:
                run_kwargs["model_settings"] = model_parameters

            logger.info(
                "LLM runtime start (provider=%s, model=%s, tools=%d)",
                provider_name,
                model_name,
                len(tools or []),
            )
            result = await agent.run(user_prompt, **run_kwargs)
        except LLMRuntimeError:
            raise
        except Exception as exc:
            raise LLMRuntimeError(str(exc)) from exc

        output = result.output if hasattr(result, "output") else result
        payload = _coerce_output_payload(output)

        usage = getattr(result, "usage", None)
        if callable(usage):
            usage = usage()
        tokens_prompt: int | None = None
        tokens_completion: int | None = None
        requests: int | None = None
        tool_calls: int | None = None
        if usage is not None:
            tokens_prompt, tokens_completion = _extract_usage_tokens(usage)
            requests, tool_calls = _extract_usage_counts(usage)

        logger.info(
            "LLM runtime done in %.1fs (provider=%s, model=%s, prompt_tokens=%s, completion_tokens=%s, requests=%s, tool_calls=%s)",
            perf_counter() - run_started_at,
            provider_name,
            model_name,
            tokens_prompt,
            tokens_completion,
            requests,
            tool_calls,
        )

        return LLMRuntimeResult(
            payload=payload,
            model=model_name,
            provider=provider_name,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            requests=requests,
            tool_calls=tool_calls,
        )
