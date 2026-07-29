from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from git_metadata_extractor.agents.llm._loader import load_prompt
from git_metadata_extractor.agents.llm.agent_tools.duckduckgo_search import (
    make_duckduckgo_search_tool,
)
from git_metadata_extractor.agents.llm.agent_tools.github_organization import (
    make_github_organization_metadata_tool,
)
from git_metadata_extractor.agents.llm.prompt_context import append_runtime_prompt_context
from git_metadata_extractor.agents.models import AgentResult, ProviderSet
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.observation.query_log import stamp_current_agent

_PROMPTS_PACKAGE = "git_metadata_extractor.agents.llm.critic.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")


class CriticDropSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    reason: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class CriticOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    organizations: list[CriticDropSuggestion] = Field(default_factory=list)
    persons: list[CriticDropSuggestion] = Field(default_factory=list)
    repositories: list[CriticDropSuggestion] = Field(default_factory=list)
    articles: list[CriticDropSuggestion] = Field(default_factory=list)


class LLMCriticAgentV2:
    """LLM-backed relevance critic for reconciled entities."""

    def __init__(
        self,
        *,
        llm_runtime: V2LLMRuntime | None = None,
        llm_call_timeout_seconds: float = 180.0,
        cache: ProviderCache | None = None,
    ) -> None:
        if llm_call_timeout_seconds <= 0:
            message = "llm_call_timeout_seconds must be > 0"
            raise ValueError(message)
        self._llm_runtime = llm_runtime or V2LLMRuntime()
        self._llm_call_timeout_seconds = float(llm_call_timeout_seconds)
        self._cache = cache

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        stamp_current_agent(
            name="critic_agent",
            context={"source_url": context.get("source_url")}
            if isinstance(context.get("source_url"), str)
            else {},
        )

        initial_context = context.get("initial_context")
        owner_provenance = _extract_owner_provenance(initial_context)
        llm_input = {
            "source_url": context.get("source_url"),
            "detected_type": context.get("detected_type"),
            "initial_context": initial_context if isinstance(initial_context, dict) else {},
            "owner_provenance": owner_provenance,
            "pipeline_outputs": context.get("pipeline_outputs", {}),
            "reconciled_entities": context.get("reconciled_entities", {}),
            "memberships": context.get("memberships", []),
            "contributions": context.get("contributions", []),
        }
        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True, default=str)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)
        tools = [make_duckduckgo_search_tool(cache=self._cache)]
        if providers.github is not None:
            tools.append(
                make_github_organization_metadata_tool(providers.github),
            )

        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=CriticOutput,
                    tools=tools,
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            message = (
                "llm_critic — LLM call timed out after "
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
                "drop_suggestion_count": sum(
                    len(payload.get(key, []))
                    for key in ("organizations", "persons", "repositories", "articles")
                ),
            },
        )


def _extract_owner_provenance(initial_context: Any) -> dict[str, Any]:
    if not isinstance(initial_context, dict):
        return {}

    repository_context = initial_context.get("repository")
    if not isinstance(repository_context, dict):
        return {}

    metadata = repository_context.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    owner = metadata.get("owner")
    owner_payload = deepcopy(owner) if isinstance(owner, dict) else {}
    owner_payload = {k: v for k, v in owner_payload.items() if v is not None}

    return {
        "repository_full_name": repository_context.get("full_name"),
        "repository_url": metadata.get("html_url") or metadata.get("url"),
        "owner": owner_payload,
    }
