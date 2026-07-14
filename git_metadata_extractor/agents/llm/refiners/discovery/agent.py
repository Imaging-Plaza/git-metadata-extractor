"""Additive entity-discovery refiner.

Where the per-entity refiners (org, repo, person, membership) only patch
a small whitelist of fields on existing entities, this agent is the
explicit ADDITIVE path: it inspects the assembled graph + repo context
(README, CITATION.cff, GitHub topics) and proposes entities the
rule-based pipeline missed.

The agent never removes or overwrites. Each proposal is gated by a hard
identifier requirement (ORCID / ROR / GitHub handle / DOI) and a
confidence floor so silent-on-doubt is the default.
"""

from __future__ import annotations

import asyncio
import json
import logging

from pydantic import BaseModel, ConfigDict, Field

from git_metadata_extractor.agents.llm._loader import load_prompt
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "git_metadata_extractor.agents.llm.refiners.discovery.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")


class DiscoveredPerson(BaseModel):
    """LLM proposal for a Person the rule-based path missed."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    schema_name: str = Field(alias="schema:name")
    pulse_githubUsername: str | None = Field(default=None, alias="pulse:githubUsername")
    pulse_orcidIdentifier: str | None = Field(default=None, alias="pulse:orcidIdentifier")
    schema_email: str | None = Field(default=None, alias="schema:email")
    reason: str = ""
    confidence: float = 0.0


class DiscoveredOrg(BaseModel):
    """LLM proposal for an Organization the rule-based path missed."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    schema_name: str = Field(alias="schema:name")
    pulse_ror: str | None = Field(default=None, alias="pulse:ror")
    pulse_githubOrganizationHandle: str | None = Field(
        default=None, alias="pulse:githubOrganizationHandle",
    )
    pulse_OrganizationType: str = Field(alias="pulse:OrganizationType")
    reason: str = ""
    confidence: float = 0.0


class DiscoveredArticle(BaseModel):
    """LLM proposal for a ScholarlyArticle the rule-based path missed."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    schema_name: str = Field(alias="schema:name")
    schema_identifier: str = Field(
        alias="schema:identifier",
        description="DOI URL, e.g. https://doi.org/10.xxxx/yyyy",
    )
    schema_datePublished: str | None = Field(default=None, alias="schema:datePublished")
    author_names: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0


class DiscoveryProposal(BaseModel):
    """Container for additive proposals across the three entity classes."""

    model_config = ConfigDict(populate_by_name=True)
    new_persons: list[DiscoveredPerson] = Field(default_factory=list)
    new_orgs: list[DiscoveredOrg] = Field(default_factory=list)
    new_articles: list[DiscoveredArticle] = Field(default_factory=list)


class DiscoveryRefinerInput(BaseModel):
    """Snapshot of the assembled graph + repo context for the LLM."""

    model_config = ConfigDict(populate_by_name=True)
    repo_handle: str
    readme_text: str | None = None
    citation_cff: str | None = None
    repo_description: str | None = None
    repo_topics: list[str] = Field(default_factory=list)
    # Repo-root attribution files (AUTHORS, NOTICE, pyproject.toml, …).
    aux_files: dict[str, str] = Field(default_factory=dict)
    existing_person_ids: list[str] = Field(default_factory=list)
    existing_org_ids: list[str] = Field(default_factory=list)
    existing_article_ids: list[str] = Field(default_factory=list)


_README_CAP = 8000  # the discovery call gets a bigger window than per-entity refiners
_CITATION_CAP = 4000


class DiscoveryRefinerAgent:
    """Single LLM call that proposes new Persons / Orgs / Articles."""

    def __init__(
        self,
        *,
        llm_runtime: V2LLMRuntime | None = None,
        llm_call_timeout_seconds: float = 600.0,
    ) -> None:
        if llm_call_timeout_seconds <= 0:
            message = "llm_call_timeout_seconds must be > 0"
            raise ValueError(message)
        self._llm_runtime = llm_runtime or V2LLMRuntime()
        self._llm_call_timeout_seconds = float(llm_call_timeout_seconds)

    async def run(
        self,
        *,
        refiner_input: DiscoveryRefinerInput,
    ) -> DiscoveryProposal:
        identifier = refiner_input.repo_handle or "discovery"

        payload = refiner_input.model_dump(by_alias=True, exclude_none=False)
        # Cap large fields locally — the model can still see plenty.
        if isinstance(payload.get("readme_text"), str):
            payload["readme_text"] = payload["readme_text"][:_README_CAP]
        if isinstance(payload.get("citation_cff"), str):
            payload["citation_cff"] = payload["citation_cff"][:_CITATION_CAP]
        aux = payload.get("aux_files") or {}
        if isinstance(aux, dict):
            payload["aux_files"] = {
                name: (content[:6_000] if isinstance(content, str) else content)
                for name, content in aux.items()
            }

        user_prompt = (
            "Inspect the repository context below and propose entities the "
            "rule-based pipeline missed. Hard rules: confidence >= 0.7; never "
            "duplicate an id in `existing_*_ids`; every proposal needs an "
            "unambiguous identifier (ORCID / ROR / github handle / DOI); "
            "every `reason` must be a verbatim quote from the README or "
            "CITATION.cff. Return empty lists when nothing meets the bar.\n\n"
            "```json\n"
            + json.dumps(payload, ensure_ascii=True, sort_keys=True)
            + "\n```"
        )

        logger.info(
            "%s — calling discovery refiner LLM "
            "(persons=%d, orgs=%d, articles=%d in graph)",
            identifier,
            len(refiner_input.existing_person_ids),
            len(refiner_input.existing_org_ids),
            len(refiner_input.existing_article_ids),
        )
        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=DiscoveryProposal,
                    tools=[],
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except TimeoutError as exc:
            message = (
                f"{identifier} — discovery refiner LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            logger.exception(message)
            raise LLMRuntimeError(message) from exc

        # The runtime returns a `LLMResult` with a `.payload` dict.
        # Re-validate against the model so callers always get the typed
        # form even if the runtime returns a dict via JSON-mode.
        if isinstance(llm_result.payload, DiscoveryProposal):
            proposal = llm_result.payload
        else:
            try:
                proposal = DiscoveryProposal.model_validate(llm_result.payload)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "%s — discovery refiner returned unparseable payload; "
                    "treating as empty",
                    identifier,
                )
                proposal = DiscoveryProposal()

        logger.info(
            "%s — discovery refiner proposed persons=%d, orgs=%d, articles=%d",
            identifier,
            len(proposal.new_persons),
            len(proposal.new_orgs),
            len(proposal.new_articles),
        )
        return proposal
