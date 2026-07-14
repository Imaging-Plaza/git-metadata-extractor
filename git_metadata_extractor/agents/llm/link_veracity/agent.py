from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from git_metadata_extractor.agents.llm._loader import load_prompt
from git_metadata_extractor.agents.llm.agent_tools.selenium_fetch import (
    make_fetch_link_content_tool,
)
from git_metadata_extractor.agents.llm.prompt_context import append_runtime_prompt_context
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime
from git_metadata_extractor.agents.models import AgentResult, ProviderSet
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.observation.query_log import stamp_current_agent

_PROMPTS_PACKAGE = "git_metadata_extractor.agents.llm.link_veracity.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")

logger = logging.getLogger(__name__)


class LinkVeracityOutput(BaseModel):
    """Structured output for link veracity checks."""

    model_config = ConfigDict(extra="ignore")

    link: str = Field(..., description="The exact URL being verified.")
    relationship_supported: bool = Field(
        ...,
        description="True if fetched content supports the source->predicate->link relation.",
    )
    relationship_summary: str | None = Field(
        None,
        description="Short rationale for yes/no decision.",
    )
    fetched_successfully: bool | None = Field(
        None,
        description="Whether the Selenium fetch succeeded.",
    )


def _resolve_link(context: dict[str, Any]) -> str:
    value = context.get("link")
    if isinstance(value, str) and value.strip():
        return value.strip()
    message = "Link veracity context is missing link"
    raise ValueError(message)


class LLMLinkVeracityAgentV2:
    """LLM-backed link verifier that evaluates relation veracity against fetched content."""

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
        self._fetch_tool = make_fetch_link_content_tool(cache)

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        del providers
        link = _resolve_link(context)

        stamp_current_agent(
            name="link_veracity_agent",
            context={"link": link},
        )

        llm_input = {
            "link": link,
            "source_entity_id": context.get("source_entity_id"),
            "predicate": context.get("predicate"),
            "relationships": context.get("relationships"),
            "source_url": context.get("source_url"),
        }

        cache_key: str | None = None
        if self._cache is not None:
            cache_key = ProviderCache.make_key(
                "link_veracity",
                "verdict",
                **llm_input,
            )
            cached_payload = self._cache.get(cache_key)
            if isinstance(cached_payload, dict):
                logger.info(
                    "link_veracity cache hit — link=%r supported=%s",
                    link,
                    bool(cached_payload.get("relationship_supported")),
                )
                return AgentResult(
                    data=dict(cached_payload),
                    warnings=[],
                    raw_output=deepcopy(cached_payload),
                    model=None,
                    provider=None,
                    tokens_prompt=0,
                    tokens_completion=0,
                    stats={
                        "agent_runtime": "llm",
                        "link": link,
                        "relationship_supported": bool(
                            cached_payload.get("relationship_supported"),
                        ),
                        "cached": True,
                    },
                )

        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True, default=str)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)

        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=LinkVeracityOutput,
                    tools=[self._fetch_tool],
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            message = (
                f"{link} — LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            raise LLMRuntimeError(message) from exc
        except LLMRuntimeError:
            raise
        except Exception as exc:
            raise LLMRuntimeError(str(exc)) from exc

        payload = dict(llm_result.payload)
        if "fetched_successfully" not in payload:
            payload["fetched_successfully"] = None
        if "relationship_summary" not in payload:
            payload["relationship_summary"] = None
        raw_output = deepcopy(payload)

        if cache_key is not None and self._cache is not None:
            self._cache.set(cache_key, payload)

        return AgentResult(
            data=payload,
            warnings=[],
            raw_output=raw_output,
            model=llm_result.model,
            provider=llm_result.provider,
            tokens_prompt=llm_result.tokens_prompt,
            tokens_completion=llm_result.tokens_completion,
            stats={
                "agent_runtime": "llm",
                "link": link,
                "relationship_supported": bool(payload.get("relationship_supported")),
            },
        )
