"""LLM-decided org-to-org parent/child relationships.

Single LLM call per request. Sees the complete set of organizations in the
extraction graph and emits a list of `(child_id, parent_id)` edges. The
caller validates the edges (existence, no self-loops, no cycles) before
stamping `org:unitOf` / `org:hasUnit` on the entities.
"""
from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from git_metadata_extractor.agents.llm._loader import load_prompt
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime
from git_metadata_extractor.agents.models import AgentResult, ProviderSet
from git_metadata_extractor.observation.query_log import stamp_current_agent

_PROMPTS_PACKAGE = "git_metadata_extractor.agents.llm.org_relationships.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")

logger = logging.getLogger(__name__)


class OrgRelationship(BaseModel):
    model_config = ConfigDict(extra="ignore")

    child_id: str = Field(..., description="ID of the sub-unit organization.")
    parent_id: str = Field(..., description="ID of the parent organization.")
    reason: str | None = Field(None, description="Short rationale citing evidence.")


class OrgRelationshipsOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    relationships: list[OrgRelationship] = Field(default_factory=list)


class LLMOrgRelationshipsAgentV2:
    """LLM agent that decides `org:unitOf` edges across a graph's organizations."""

    def __init__(
        self,
        *,
        llm_runtime: V2LLMRuntime | None = None,
        llm_call_timeout_seconds: float = 120.0,
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
        organizations = context.get("organizations")
        if not isinstance(organizations, list) or len(organizations) < 2:
            return AgentResult(
                data={"relationships": []},
                warnings=[],
                raw_output={"relationships": []},
                model=None,
                provider=None,
                tokens_prompt=0,
                tokens_completion=0,
                stats={"agent_runtime": "llm", "skipped": "fewer_than_two_orgs"},
            )

        stamp_current_agent(
            name="org_relationships_agent",
            context={"org_count": len(organizations)},
        )

        llm_input = {
            "source_url": context.get("source_url"),
            "organizations": organizations,
        }
        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True, default=str)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)

        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=OrgRelationshipsOutput,
                    tools=None,
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            message = (
                "org_relationships — LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            raise LLMRuntimeError(message) from exc
        except LLMRuntimeError:
            raise
        except Exception as exc:
            raise LLMRuntimeError(str(exc)) from exc

        payload = llm_result.payload if isinstance(llm_result.payload, dict) else {}
        relationships = payload.get("relationships")
        if not isinstance(relationships, list):
            relationships = []
        clean_relationships: list[dict[str, Any]] = []
        for entry in relationships:
            if not isinstance(entry, dict):
                continue
            child_id = entry.get("child_id")
            parent_id = entry.get("parent_id")
            if not isinstance(child_id, str) or not isinstance(parent_id, str):
                continue
            if not child_id.strip() or not parent_id.strip():
                continue
            clean_relationships.append(
                {
                    "child_id": child_id.strip(),
                    "parent_id": parent_id.strip(),
                    "reason": entry.get("reason")
                    if isinstance(entry.get("reason"), str)
                    else None,
                },
            )

        normalized = {"relationships": clean_relationships}
        return AgentResult(
            data=normalized,
            warnings=[],
            raw_output=deepcopy(payload),
            model=llm_result.model,
            provider=llm_result.provider,
            tokens_prompt=llm_result.tokens_prompt,
            tokens_completion=llm_result.tokens_completion,
            stats={
                "agent_runtime": "llm",
                "org_count": len(organizations),
                "edge_count": len(clean_relationships),
            },
        )


__all__ = ["LLMOrgRelationshipsAgentV2"]
