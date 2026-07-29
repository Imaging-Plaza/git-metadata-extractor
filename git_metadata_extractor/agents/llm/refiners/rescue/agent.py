"""Affiliation-rescue refiner: LLM decides which dropped Memberships to bring back.

Companion to the additive `discovery` refiner. Where discovery proposes
entities that never existed in the rule-based output, rescue inspects
the `(person, org)` pairs that the deterministic Membership filter
DROPPED (no role / no dates / no ORCID+ROR anchor) and lets the LLM
flip the verdict per pair when the README explicitly supports it.

Single LLM call per extract: the model gets the README + the dropped
candidates + the existing-org id list, and returns `{decisions: [...]}`
where each accepted decision identifies the original `membership_id`.
The caller (refine_with_llm) materialises the rescued memberships and,
when needed, synthesises a minimal Organization stub so the link
resolves.
"""

from __future__ import annotations

import asyncio
import json
import logging

from pydantic import BaseModel, ConfigDict, Field

from git_metadata_extractor.agents.llm._loader import load_prompt
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "git_metadata_extractor.agents.llm.refiners.rescue.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")

_README_CAP = 8000
_CITATION_CAP = 4000


class RescueCandidate(BaseModel):
    """One dropped (person, org) pair handed to the rescue LLM."""

    model_config = ConfigDict(populate_by_name=True)
    membership_id: str
    person_id: str
    person_name: str | None = None
    org_id: str | None = None
    org_name: str | None = None
    reason: str | None = None


class RescueDecision(BaseModel):
    """Per-candidate LLM verdict — accept-only (rejects omitted)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    membership_id: str
    accept: bool = True
    reason: str = ""
    confidence: float = 0.0


class RescueRefinerInput(BaseModel):
    """Context payload for the rescue LLM call."""

    model_config = ConfigDict(populate_by_name=True)
    repo_handle: str = ""
    readme_text: str | None = None
    citation_cff: str | None = None
    # Auxiliary attribution files (AUTHORS, NOTICE.yml, pyproject.toml,
    # CONTRIBUTING.md, …). Keyed by filename, capped per-file upstream.
    aux_files: dict[str, str] = Field(default_factory=dict)
    candidates: list[RescueCandidate] = Field(default_factory=list)
    existing_org_ids: list[str] = Field(default_factory=list)


class RescueRefinerOutput(BaseModel):
    """LLM response — list of accept decisions, one per rescued candidate."""

    model_config = ConfigDict(populate_by_name=True)
    decisions: list[RescueDecision] = Field(default_factory=list)


class RescueRefinerAgent:
    """LLM agent that votes on which dropped affiliations to reinstate."""

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
        *,
        refiner_input: RescueRefinerInput,
    ) -> RescueRefinerOutput:
        identifier = refiner_input.repo_handle or "rescue"
        if not refiner_input.candidates:
            return RescueRefinerOutput()

        payload = refiner_input.model_dump(by_alias=True, exclude_none=False)
        if isinstance(payload.get("readme_text"), str):
            payload["readme_text"] = payload["readme_text"][:_README_CAP]
        if isinstance(payload.get("citation_cff"), str):
            payload["citation_cff"] = payload["citation_cff"][:_CITATION_CAP]
        # Per-file cap on aux files to keep the prompt budget honest.
        # AUTHORS / NOTICE / pyproject.toml are typically the richest
        # signal for affiliations; we keep their first 6KB each.
        aux = payload.get("aux_files") or {}
        if isinstance(aux, dict):
            payload["aux_files"] = {
                name: (content[:6_000] if isinstance(content, str) else content)
                for name, content in aux.items()
            }

        user_prompt = (
            "Decide which of the dropped (person, org) memberships below "
            "should be reinstated based on README/CITATION evidence. "
            "Output only accept decisions; reject by omission. Confidence "
            "must be >= 0.7 and the `reason` must be a verbatim README quote.\n\n"
            "```json\n"
            + json.dumps(payload, ensure_ascii=True, sort_keys=True)
            + "\n```"
        )

        logger.info(
            "%s — calling rescue refiner LLM (candidates=%d)",
            identifier,
            len(refiner_input.candidates),
        )
        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=RescueRefinerOutput,
                    tools=[],
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except TimeoutError as exc:
            message = (
                f"{identifier} — rescue refiner LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            logger.exception(message)
            raise LLMRuntimeError(message) from exc

        if isinstance(llm_result.payload, RescueRefinerOutput):
            output = llm_result.payload
        else:
            try:
                output = RescueRefinerOutput.model_validate(llm_result.payload)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "%s — rescue refiner returned unparseable payload; "
                    "treating as empty",
                    identifier,
                )
                output = RescueRefinerOutput()

        logger.info(
            "%s — rescue refiner accepted %d / %d candidates",
            identifier,
            len(output.decisions),
            len(refiner_input.candidates),
        )
        return output
