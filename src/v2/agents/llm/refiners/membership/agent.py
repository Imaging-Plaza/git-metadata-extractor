from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "src.v2.agents.llm.refiners.membership.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")


class MembershipRefinerInput(BaseModel):
    """Snapshot passed to the LLM membership refiner."""

    entity: dict[str, Any] = Field(
        description="The org:Membership entity from the rule-based pipeline.",
    )
    organization_name: str | None = Field(
        default=None,
        description=(
            "Name of the organization this membership points to (looked up from "
            "org:organization). Used by the prompt to detect role suffixes that "
            "duplicate the org name."
        ),
    )


class MembershipRefinerPatch(BaseModel):
    """Patch returned by the refiner. Only whitelisted fields may be set."""

    role: str | None = Field(
        default=None,
        alias="org:role",
        description="Canonical role title, or null to leave untouched.",
    )

    model_config = {"populate_by_name": True}


class MembershipRefinerAgent:
    """LLM agent that proposes a canonical org:role string for an org:Membership."""

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
        *,
        refiner_input: MembershipRefinerInput,
    ) -> dict[str, Any]:
        identifier = refiner_input.entity.get("id") or refiner_input.entity.get("@id", "?")

        user_payload = {
            "entity": refiner_input.entity,
            "organization_name": refiner_input.organization_name,
        }
        user_prompt = (
            "Inspect the membership entity below and propose a JSON patch with "
            "`org:role` only when the current value has clear cleanup opportunity. "
            "Return `{}` if no change is warranted.\n\n"
            "```json\n"
            + json.dumps(user_payload, ensure_ascii=True, sort_keys=True)
            + "\n```"
        )

        logger.info("%s — calling membership refiner LLM", identifier)
        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=MembershipRefinerPatch,
                    tools=[],
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except TimeoutError as exc:
            message = (
                f"{identifier} — membership refiner LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            logger.exception(message)
            raise LLMRuntimeError(message) from exc

        patch = {
            key: value
            for key, value in llm_result.payload.items()
            if value is not None and key == "org:role"
        }
        logger.info(
            "%s — membership refiner returned %s",
            identifier,
            list(patch.keys()) or "no change",
        )
        return patch
