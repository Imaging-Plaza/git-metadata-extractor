from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.prompt_context import append_runtime_prompt_context
from src.v2.agents.models import AgentResult, ProviderSet
from src.v2.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

_PROMPTS_PACKAGE = "src.v2.agents.llm.dedup.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")


class DedupClusterSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class DedupOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    organizations: list[DedupClusterSuggestion] = Field(default_factory=list)
    persons: list[DedupClusterSuggestion] = Field(default_factory=list)
    repositories: list[DedupClusterSuggestion] = Field(default_factory=list)
    articles: list[DedupClusterSuggestion] = Field(default_factory=list)


class LLMDedupAgentV2:
    """LLM-backed global dedup candidate generator."""

    def __init__(
        self,
        *,
        llm_runtime: V2LLMRuntime | None = None,
        llm_call_timeout_seconds: float = 180.0,
    ) -> None:
        if llm_call_timeout_seconds <= 0:
            message = "llm_call_timeout_seconds must be > 0"
            raise ValueError(message)
        self._llm_runtime = llm_runtime or V2LLMRuntime()
        self._llm_call_timeout_seconds = float(llm_call_timeout_seconds)

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        del providers
        llm_input = {
            "source_url": context.get("source_url"),
            "detected_type": context.get("detected_type"),
            "typed_entity_buckets": context.get("typed_entity_buckets", {}),
        }
        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True, default=str)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)

        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=DedupOutput,
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            message = (
                "llm_dedup — LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            raise LLMRuntimeError(message) from exc
        except LLMRuntimeError:
            raise
        except Exception as exc:
            raise LLMRuntimeError(str(exc)) from exc

        payload = dict(llm_result.payload)
        for key in ("organizations", "persons", "repositories", "articles"):
            value = payload.get(key)
            if not isinstance(value, list):
                payload[key] = []

        return AgentResult(
            data=payload,
            warnings=[],
            raw_output=deepcopy(payload),
            model=llm_result.model,
            provider=llm_result.provider,
            tokens_prompt=llm_result.tokens_prompt,
            tokens_completion=llm_result.tokens_completion,
            stats={
                "agent_runtime": "llm",
                "cluster_suggestion_count": sum(
                    len(payload.get(key, []))
                    for key in ("organizations", "persons", "repositories", "articles")
                ),
            },
        )
