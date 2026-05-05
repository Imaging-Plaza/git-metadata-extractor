from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "src.v2.agents.llm.refiners.repository.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")

RepositoryTypeLiteral = Literal[
    "pulse:Software",
    "pulse:EducationalResource",
    "pulse:Documentation",
    "pulse:Data",
    "pulse:Other",
]


class RepositoryRefinerInput(BaseModel):
    """Snapshot passed to the LLM repository refiner."""

    entity: dict[str, Any] = Field(
        description="The schema:SoftwareSourceCode entity from the rule-based pipeline.",
    )
    repo_context_summary: dict[str, Any] | None = Field(
        default=None,
        description="Small summary of the source repo (name, description, README excerpt).",
    )


class RepositoryRefinerPatch(BaseModel):
    """Patch returned by the refiner. Only whitelisted fields may be set."""

    discipline: list[str] | None = Field(
        default=None,
        alias="pulse:discipline",
        description="Wikidata QIDs (e.g., ['wd:Q428691']). At most 2.",
    )
    repository_type: RepositoryTypeLiteral | None = Field(
        default=None,
        alias="pulse:repositoryType",
        description="Only set when current value is pulse:Other.",
    )

    model_config = {"populate_by_name": True}


class RepositoryRefinerAgent:
    """LLM agent that proposes a small JSON patch for a schema:SoftwareSourceCode entity."""

    def __init__(
        self,
        *,
        llm_runtime: V2LLMRuntime | None = None,
        llm_call_timeout_seconds: float = 60.0,
    ) -> None:
        if llm_call_timeout_seconds <= 0:
            message = "llm_call_timeout_seconds must be > 0"
            raise ValueError(message)
        self._llm_runtime = llm_runtime or V2LLMRuntime()
        self._llm_call_timeout_seconds = float(llm_call_timeout_seconds)

    async def run(
        self,
        *,
        refiner_input: RepositoryRefinerInput,
        tools: list[Any] | None = None,
    ) -> dict[str, Any]:
        identifier = refiner_input.entity.get("schema:name") or refiner_input.entity.get(
            "id",
            "?",
        )

        user_payload = {
            "entity": refiner_input.entity,
            "repo_context_summary": refiner_input.repo_context_summary or {},
        }
        user_prompt = (
            "Inspect the repository entity below and propose a JSON patch with "
            "only the fields you want to change (whitelist: `pulse:discipline`, "
            "`pulse:repositoryType` — the latter only when current is `pulse:Other`). "
            "Return `{}` if no change is warranted.\n\n"
            "```json\n"
            + json.dumps(user_payload, ensure_ascii=True, sort_keys=True)
            + "\n```"
        )

        logger.info(
            "%s — calling repo refiner LLM (%d tool(s))",
            identifier,
            len(tools or []),
        )
        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=RepositoryRefinerPatch,
                    tools=tools or [],
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except TimeoutError as exc:
            message = (
                f"{identifier} — repo refiner LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            logger.exception(message)
            raise LLMRuntimeError(message) from exc

        whitelist = {"pulse:discipline", "pulse:repositoryType"}
        patch = {
            key: value
            for key, value in llm_result.payload.items()
            if value is not None and key in whitelist
        }
        logger.info(
            "%s — repo refiner returned %s",
            identifier,
            list(patch.keys()) or "no change",
        )
        return patch
