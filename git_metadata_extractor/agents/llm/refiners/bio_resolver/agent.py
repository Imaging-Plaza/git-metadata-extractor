"""Bio resolver — LLM uses ROR RAG to anchor persons whose affiliation
only surfaces in free-text bio / profile-readme / ORCID biography.

Companion to the deterministic ``resolve_bio_to_ror`` stage. The
regex / domain-hints pass catches every easy case (verbatim "at X" in
bio, institutional email/blog host). What's left after that is the
long tail — bios that name an affiliation in prose the rule-based
extractor can't reliably parse:

  - "Currently a Senior Software Engineer Manager working in Identity"
    (no `at <Org>` shape)
  - "Building distributed systems for the next-gen LIGO collaboration"
    (the org is "LIGO" but it's syntactically buried)
  - Long GitHub profile READMEs where the affiliation lives 800
    characters in.

This LLM stage runs only under the hybrid / LLM runtime. The acceptance
gate is the same as the deterministic stage's (top-1 ROR >= 0.55,
org-type allowlist, plus ≥ 0.7 LLM confidence and a verbatim text
quote in `reason`). When the model can't clear the bar it returns
all-nulls and the caller leaves the person unaffiliated.
"""

from __future__ import annotations

import asyncio
import json
import logging

from pydantic import BaseModel, ConfigDict, Field

from git_metadata_extractor.agents.llm._loader import load_prompt
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "git_metadata_extractor.agents.llm.refiners.bio_resolver.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")


class UnresolvedPerson(BaseModel):
    """One person whose affiliation the deterministic stage missed."""

    model_config = ConfigDict(populate_by_name=True)
    person_id: str = Field(description="@id of the person (likely a github URL).")
    schema_name: str | None = Field(default=None, alias="schema:name")
    bio: str | None = None
    orcid_biography: str | None = None
    profile_readme: str | None = None
    location: str | None = None
    orcid_country: str | None = Field(
        default=None,
        description="ISO 3166-1 alpha-2 country code from ORCID, for ROR disambiguation.",
    )


class BioResolverPatch(BaseModel):
    """LLM patch for one Person. ``pulse_ror=None`` means no-op."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    pulse_ror: str | None = Field(default=None, alias="pulse:ror")
    reason: str = Field(
        default="",
        description=(
            "Verbatim quote from bio / orcid / readme that names the org. "
            "Required when pulse_ror is non-null."
        ),
        max_length=400,
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class BioResolverInput(BaseModel):
    """Context payload for one resolver call."""

    model_config = ConfigDict(populate_by_name=True)
    person: UnresolvedPerson


# Caller-side confidence floor. The system prompt also tells the model
# to enforce this, but we re-check defensively — the validator can't
# trust the model alone.
CONFIDENCE_FLOOR: float = 0.7

# Long READMEs are sent through verbatim but capped so we don't pay
# token cost on a 50k-char profile. 8 KB matches the discovery-refiner
# cap and is enough to cover the affiliation paragraph for ~99% of
# real READMEs spot-checked on github.com.
PROFILE_README_CAP_CHARS: int = 8_000


class BioResolverAgent:
    """Single-person bio resolver call with `search_ror_rag` tool access."""

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
        refiner_input: BioResolverInput,
        tools: list[object] | None = None,
    ) -> BioResolverPatch:
        identifier = refiner_input.person.schema_name or refiner_input.person.person_id

        payload = refiner_input.model_dump(by_alias=True, exclude_none=False)
        person_payload = payload.get("person")
        if isinstance(person_payload, dict):
            readme = person_payload.get("profile_readme")
            if isinstance(readme, str):
                person_payload["profile_readme"] = readme[:PROFILE_README_CAP_CHARS]

        user_prompt = (
            "Resolve the Person below to a single ROR identifier when the "
            "bio / orcid / readme text contains a verbatim mention of an "
            "affiliation that the search_ror_rag tool confirms. Return "
            "all-nulls when no candidate clears the bar in the system "
            "prompt.\n\n"
            "```json\n"
            + json.dumps(payload, ensure_ascii=True, sort_keys=True)
            + "\n```"
        )

        logger.info(
            "%s — calling bio_resolver LLM (%d tool(s))",
            identifier,
            len(tools or []),
        )
        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=BioResolverPatch,
                    tools=list(tools or []),
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except TimeoutError as exc:
            message = (
                f"{identifier} — bio_resolver LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            logger.exception(message)
            raise LLMRuntimeError(message) from exc

        if isinstance(llm_result.payload, BioResolverPatch):
            patch = llm_result.payload
        else:
            try:
                patch = BioResolverPatch.model_validate(llm_result.payload)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "%s — bio_resolver returned unparseable payload; treating as no-op",
                    identifier,
                )
                patch = BioResolverPatch()

        # Defensive floor: if the model emits a ROR with low confidence
        # we clear the ROR. Treating low confidence as a no-op (rather
        # than rejecting the whole patch) keeps the `reason` for logging.
        if patch.pulse_ror and patch.confidence < CONFIDENCE_FLOOR:
            logger.info(
                "%s — bio_resolver dropping ROR (confidence %.2f < %.2f)",
                identifier,
                patch.confidence,
                CONFIDENCE_FLOOR,
            )
            patch = BioResolverPatch(
                reason=patch.reason,
                confidence=patch.confidence,
            )

        logger.info(
            "%s — bio_resolver patch: ror=%s confidence=%.2f",
            identifier,
            patch.pulse_ror,
            patch.confidence,
        )
        return patch


__all__ = [
    "CONFIDENCE_FLOOR",
    "PROFILE_README_CAP_CHARS",
    "BioResolverAgent",
    "BioResolverInput",
    "BioResolverPatch",
    "UnresolvedPerson",
]
