"""ROR parent selector — an LLM picks the one correct ROR parent.

`infer_github_handle_parents` (see ``git_metadata_extractor/pipeline/stages/ownership_check.py``)
fuzzy-searches ROR for every github-only organization in the graph. Token
overlap alone is far too lax: a single shared word makes `imaging-plaza`
match "Plaza Community Services" (US) and `epfl-lasa` match NCAR (US),
LabEx PERSYVAL (FR) and three other unrelated labs. The historical stage
attached the top-5 hits to the graph and stamped the highest-scoring one
as the `org:unitOf` parent — so a real deployment saw 9/10 organizations
get a wrong ROR.

This agent hands the *already fetched* ROR candidates to an LLM and asks
it to pick the single ROR organization that is genuinely the github
org's home / parent institution — or decline. It mirrors the repository
refiner pattern: a small typed input, a whitelisted typed verdict, and a
graceful no-op on any failure. The agent can only return one of the
candidate ids verbatim (or null) — it never invents a ROR id.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from git_metadata_extractor.agents.llm._loader import load_prompt
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "git_metadata_extractor.agents.llm.refiners.ror_parent.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")

# A ROR pick is only honoured at/above this confidence. Mirrors the
# org_resolver floor — below it, decline rather than guess.
MIN_SELECTION_CONFIDENCE = 0.7

# README excerpts can be large; cap what we put in the prompt.
_README_EXCERPT_LIMIT = 2_000


class RorCandidate(BaseModel):
    """One ROR organization offered to the selector."""

    model_config = ConfigDict(populate_by_name=True)

    ror_id: str = Field(description="ROR id URL, e.g. https://ror.org/02s376052.")
    name: str = Field(description="Canonical ROR display name.")
    aliases: list[str] = Field(default_factory=list)
    acronyms: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    country: str | None = Field(default=None, description="Country name, when known.")
    token_overlap_score: int = Field(
        default=0,
        description="Deterministic token-overlap score that surfaced this candidate "
        "(a hint only — the top score is frequently wrong).",
    )


class RorParentSelectorInput(BaseModel):
    """Context for one github-org → ROR-parent selection call."""

    model_config = ConfigDict(populate_by_name=True)

    github_handle: str
    github_org_name: str | None = None
    # The github org's own metadata (description / homepage / location).
    # The `description` is frequently decisive — it names the parent.
    org_context: dict[str, Any] | None = None
    candidates: list[RorCandidate]


class RorParentSelectorPatch(BaseModel):
    """LLM verdict. ``ror_id`` null means 'none of these is the parent'."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    ror_id: str | None = Field(
        default=None,
        description="The chosen candidate's ROR id (verbatim), or null to decline.",
    )
    reason: str = Field(
        default="",
        description="Verbatim evidence from the chosen candidate.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    def accepted_ror_id(self) -> str | None:
        """Return the chosen ROR id only when it clears the confidence floor."""
        if self.ror_id and self.confidence >= MIN_SELECTION_CONFIDENCE:
            return self.ror_id
        return None


class RorParentSelectorAgent:
    """LLM agent that picks the one correct ROR parent (or declines)."""

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
        refiner_input: RorParentSelectorInput,
        tools: list[Any] | None = None,
    ) -> RorParentSelectorPatch:
        """Return the selector's verdict. Never raises for an empty/bad LLM
        reply — only a hard timeout propagates as ``LLMRuntimeError``."""

        identifier = refiner_input.github_handle
        candidate_ids = {candidate.ror_id for candidate in refiner_input.candidates}
        if not candidate_ids:
            return RorParentSelectorPatch()

        payload = refiner_input.model_dump(by_alias=True, exclude_none=False)
        org_context = payload.get("org_context")
        if isinstance(org_context, dict):
            for key in ("description", "homepage", "location", "company", "profile_readme"):
                value = org_context.get(key)
                if isinstance(value, str) and len(value) > _README_EXCERPT_LIMIT:
                    org_context[key] = value[:_README_EXCERPT_LIMIT]

        user_prompt = (
            "Pick the one ROR organization that is genuinely the parent / home "
            "institution of the GitHub org below, or return `ror_id: null` when "
            "none of the candidates is. Follow the geography and "
            "coincidental-collision rules in the system prompt. `ror_id` must be "
            "copied verbatim from a candidate; confidence must be ≥ 0.7 for a "
            "non-null pick.\n\n"
            "```json\n"
            + json.dumps(payload, ensure_ascii=True, sort_keys=True)
            + "\n```"
        )

        logger.info(
            "%s — calling ror_parent selector LLM (%d candidate(s), %d tool(s))",
            identifier,
            len(candidate_ids),
            len(tools or []),
        )
        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=RorParentSelectorPatch,
                    tools=tools or [],
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except TimeoutError as exc:
            message = (
                f"{identifier} — ror_parent selector LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            logger.exception(message)
            raise LLMRuntimeError(message) from exc

        if isinstance(llm_result.payload, RorParentSelectorPatch):
            patch = llm_result.payload
        else:
            try:
                patch = RorParentSelectorPatch.model_validate(llm_result.payload)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "%s — ror_parent selector returned an unparseable payload; "
                    "treating as a decline",
                    identifier,
                )
                return RorParentSelectorPatch()

        # Hard guard: never honour an invented ROR id. The LLM may only
        # pick from the candidates we fetched deterministically.
        if patch.ror_id is not None and patch.ror_id not in candidate_ids:
            logger.warning(
                "%s — ror_parent selector returned non-candidate id %r; discarding",
                identifier,
                patch.ror_id,
            )
            return RorParentSelectorPatch(reason=patch.reason, confidence=0.0)

        logger.info(
            "%s — ror_parent selector picked ror=%s confidence=%.2f",
            identifier,
            patch.ror_id,
            patch.confidence,
        )
        return patch


__all__ = [
    "MIN_SELECTION_CONFIDENCE",
    "RorCandidate",
    "RorParentSelectorAgent",
    "RorParentSelectorInput",
    "RorParentSelectorPatch",
]
