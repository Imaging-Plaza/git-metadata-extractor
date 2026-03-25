from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.agent_tools.selenium_fetch import (
    fetch_link_content_via_selenium_tool,
)
from src.v2.agents.llm.prompt_context import append_runtime_prompt_context
from src.v2.agents.models import AgentResult, ProviderSet
from src.v2.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

_PROMPTS_PACKAGE = "src.v2.agents.llm.link_veracity.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")


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
        link = _resolve_link(context)

        llm_input = {
            "link": link,
            "source_entity_id": context.get("source_entity_id"),
            "predicate": context.get("predicate"),
            "relationships": context.get("relationships"),
            "source_url": context.get("source_url"),
        }
        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True, default=str)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)

        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=LinkVeracityOutput,
                    tools=[fetch_link_content_via_selenium_tool],
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
