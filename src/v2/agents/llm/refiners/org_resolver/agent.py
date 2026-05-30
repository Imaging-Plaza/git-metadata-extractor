"""Org resolver — LLM uses RAG tools to anchor un-identified Organizations.

Companion to the discovery and rescue refiners. The rule-based
org_agent enforces a strict "name token overlap" guard against ROR/
Infoscience hits (commit history: phantom-org incidents like SDSC ->
San Diego Supercomputer when the query was Swiss Data Science Center).
That guard is correct most of the time but too blunt for structured
inputs — Infoscience codes (`UPMWMATHIS`), GitHub handles concatenated
by ORCID (`@dynamical-inference @ki-macht-schule …`), and
`<parent>, <unit>` composites (`CNRS, IGF`) all fail the guard despite
being resolvable with the right tool routing.

This LLM stage runs AFTER the rule-based path. It only sees the Orgs
that are about to be SHACL-rejected (no `pulse:ror`, no
`pulse:githubOrganizationHandle`, no `pulse:infoscienceOrganizationIdentifier`,
no `schema:identifier`) and is given tools to search ROR/Infoscience/
EPFL Graph plus the live GitHub metadata endpoint. The LLM proposes a
patch with the canonical identifier(s); the caller applies it before
strict validation runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "src.v2.agents.llm.refiners.org_resolver.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")


OrganizationTypeLiteral = Literal[
    "pulse:University",
    "pulse:ResearchInstitution",
    "pulse:GovernmentAgency",
    "pulse:SoftwareProject",
    "pulse:PrivateCompany",
    "pulse:NonProfitOrganization",
    "pulse:CommunitySpace",
    "pulse:OtherOrganizationType",
]


class UnresolvedOrg(BaseModel):
    """One un-anchored Org handed to the resolver LLM."""

    model_config = ConfigDict(populate_by_name=True)
    org_id: str = Field(description="Current @id of the org (likely a UUID).")
    schema_name: str = Field(alias="schema:name")
    org_type: str | None = Field(default=None, alias="pulse:OrganizationType")
    affiliated_person_names: list[str] = Field(default_factory=list)


class OrgResolverPatch(BaseModel):
    """LLM patch for one Org. All fields optional — null means "leave alone"."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    pulse_ror: str | None = Field(default=None, alias="pulse:ror")
    pulse_infoscienceOrganizationIdentifier: str | None = Field(
        default=None,
        alias="pulse:infoscienceOrganizationIdentifier",
    )
    pulse_githubOrganizationHandle: str | None = Field(
        default=None,
        alias="pulse:githubOrganizationHandle",
    )
    schema_identifier: str | None = Field(default=None, alias="schema:identifier")
    org_unitOf: str | None = Field(default=None, alias="org:unitOf")
    pulse_OrganizationType: OrganizationTypeLiteral | None = Field(
        default=None,
        alias="pulse:OrganizationType",
    )
    schema_name_canonical: str | None = Field(
        default=None,
        alias="schema:name_canonical",
    )
    reason: str = ""
    confidence: float = 0.0


class OrgResolverInput(BaseModel):
    """Context payload for the resolver call."""

    model_config = ConfigDict(populate_by_name=True)
    repo_handle: str = ""
    repo_description: str | None = None
    readme_excerpt: str | None = None
    org: UnresolvedOrg
    # Pre-fetched evidence: the caller fans the query out to Infoscience
    # (exact code lookup), GitHub (handle resolution), ROR (composite
    # parent/unit), and the federated RAG before invoking the LLM. The
    # model then has concrete candidates to cite verbatim instead of
    # having to discover the right tool routing on its own.
    pre_fetched_evidence: dict[str, Any] = Field(default_factory=dict)


class OrgResolverAgent:
    """Single-Org resolver call with tool access to ROR/Infoscience/GitHub."""

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
        refiner_input: OrgResolverInput,
        tools: list[Any] | None = None,
    ) -> OrgResolverPatch:
        identifier = refiner_input.org.schema_name or refiner_input.org.org_id

        payload = refiner_input.model_dump(by_alias=True, exclude_none=False)
        if isinstance(payload.get("readme_excerpt"), str):
            payload["readme_excerpt"] = payload["readme_excerpt"][:4_000]

        user_prompt = (
            "Resolve the un-anchored Organization below to a canonical identifier "
            "using the search tools. Follow the routing rules in the system prompt "
            "(GitHub handle → GitHub metadata; uppercase code → Infoscience; "
            "comma-composite → ROR for parent + unit; otherwise → ROR then "
            "Infoscience). Confidence must be ≥ 0.7 for any non-null field; the "
            "`reason` must be a verbatim quote from a tool result. Return all-nulls "
            "when no tool result clears the bar.\n\n"
            "```json\n"
            + json.dumps(payload, ensure_ascii=True, sort_keys=True)
            + "\n```"
        )

        logger.info(
            "%s — calling org_resolver LLM (%d tool(s))",
            identifier,
            len(tools or []),
        )
        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=OrgResolverPatch,
                    tools=tools or [],
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except TimeoutError as exc:
            message = (
                f"{identifier} — org_resolver LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            logger.exception(message)
            raise LLMRuntimeError(message) from exc

        if isinstance(llm_result.payload, OrgResolverPatch):
            patch = llm_result.payload
        else:
            try:
                patch = OrgResolverPatch.model_validate(llm_result.payload)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "%s — org_resolver returned unparseable payload; treating as no-op",
                    identifier,
                )
                patch = OrgResolverPatch()

        logger.info(
            "%s — org_resolver patch: ror=%s infoscience=%s github=%s confidence=%.2f",
            identifier,
            patch.pulse_ror,
            patch.pulse_infoscienceOrganizationIdentifier,
            patch.pulse_githubOrganizationHandle,
            patch.confidence,
        )
        return patch
