from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.agent_tools.email_hash import hash_user_email_tool
from src.v2.agents.llm.agent_tools.infoscience_search import make_infoscience_search_tool
from src.v2.agents.llm.agent_tools.orcid_person import make_orcid_person_tool
from src.v2.agents.llm.agent_tools.selenium_fetch import (
    fetch_link_content_via_selenium_tool,
)
from src.v2.agents.llm.prompt_context import append_runtime_prompt_context
from src.v2.agents.models import AgentResult, ProviderSet, generate_uuid
from src.v2.schema.models.strict import PersonModel
from src.v2.agents.llm.runtime import (
    LLMRuntimeError,
    V2LLMRuntime,
)

_PROMPTS_PACKAGE = "src.v2.agents.llm.person.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")

README_CONTEXT_MAX_CHARS = 2000
GIMIE_JSONLD_MAX_CHARS = 4000


class _PersonIdentifiers(BaseModel):
    """Flat identifiers sub-object for LLM person output."""

    model_config = ConfigDict(extra="ignore")

    pulse_orcid: str | None = Field(None, alias="pulse:orcid")
    pulse_infosciencePersonIdentifier: str | None = Field(
        None, alias="pulse:infosciencePersonIdentifier"
    )
    pulse_githubUsername: str | None = Field(None, alias="pulse:githubUsername")
    uuid: str | None = Field(None)


class LLMPersonOutputShape(BaseModel):
    """Flat pydantic-ai output type for LLMPersonAgentV2.

    AgentPersonShape (the generated model) is a RootModel union of three
    variants — pydantic-ai cannot use union RootModels as output_type because
    their JSON schema is {"anyOf": [...]} rather than {"type": "object"}.
    This flat BaseModel mirrors the same fields and produces a valid object
    schema that pydantic-ai can enforce during structured output generation.
    Permissive validation against the full agent schema and strict validation
    against PersonModel both happen as separate post-processing steps.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Resolved hierarchical identifier.")
    type: str = Field(..., description="RDF type. Must be 'schema:Person'.")
    shacl: str = Field(..., description="SHACL shape. Must be 'pulse:PersonShape'.")
    identifiers: _PersonIdentifiers = Field(
        ..., description="All available identifiers for this person."
    )
    idSource: str = Field(
        ...,
        description=(
            "Which identifier was used as primary id. "
            "One of: 'pulse:orcid', 'pulse:infosciencePersonIdentifier', "
            "'pulse:githubUsername', 'uuid'."
        ),
    )
    schema_name: str = Field(
        ..., alias="schema:name", description="Full display name of the person."
    )
    schema_email: str | None = Field(
        None, alias="schema:email", description="Anonymized email address."
    )
    schema_url: str | None = Field(None, alias="schema:url", description="Profile URL.")
    pulse_githubUsername: str | None = Field(
        None, alias="pulse:githubUsername", description="GitHub login."
    )
    pulse_orcidIdentifier: str | None = Field(
        None, alias="pulse:orcidIdentifier", description="ORCID identifier."
    )
    pulse_infosciencePersonIdentifier: str | None = Field(
        None,
        alias="pulse:infosciencePersonIdentifier",
        description="Infoscience person UUID4.",
    )
    org_hasMembership: list[str] | None = Field(
        None,
        alias="org:hasMembership",
        description="MembershipShape IDs: personId_orgId.",
    )
    pulse_hasContribution: list[str] | None = Field(
        None,
        alias="pulse:hasContribution",
        description="ContributionShape IDs: personId_repoId.",
    )
    pulse_owns: list[str] | None = Field(
        None,
        alias="pulse:owns",
        description="RepositoryShape IDs owned by this person.",
    )


def _extract_person_identifier(context: dict[str, Any]) -> str | None:
    """Return the first usable person identifier found in context."""

    for key in ("username", "github_username", "orcid", "infoscience_id", "name"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_orcid_hint(raw: Any) -> str | None:
    """Strip ORCID URL prefix and return bare ORCID, or None."""

    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = raw.strip()
    if candidate.lower().startswith("https://orcid.org/"):
        candidate = candidate.rsplit("/", maxsplit=1)[-1]
    return candidate or None


def _strict_validate(payload: dict[str, Any]) -> list[str]:
    """Validate payload against the strict PersonModel; return warnings only."""

    warnings: list[str] = []
    try:
        PersonModel.model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors():
            field = " -> ".join(str(loc) for loc in error["loc"])
            warnings.append(f"Strict schema warning at {field}: {error['msg']}")
    return warnings


class LLMPersonAgentV2:
    """LLM-backed agent that produces a pulse:PersonShape entity from gathered
    person context.

    Data flow:
      1. Identifier extraction — reads whatever person identifiers are available
         from context (GitHub username, ORCID hint, Infoscience ID, name).
         Raises ValueError only when the context is completely empty of any
         usable identity signal.
      2. GitHub profile fetch (optional) — calls providers.github.get_user()
         only when a GitHub username is present in context.
      3. Tool construction — builds Infoscience search and ORCID lookup tools
         as closures capturing the respective providers; tools are only included
         when their provider is configured.
      4. Prompt assembly — serialises the extracted context as JSON and injects
         it into the Markdown prompt templates loaded from the ``prompts/``
         sub-package via importlib.resources.
      5. LLM call — delegates to V2LLMRuntime with ``output_type=LLMPersonOutputShape``,
         a flat BaseModel that pydantic-ai can enforce as structured output
         (first validation pass — permissive, coerces minor type mismatches).
      6. Strict validation — validates the agent-schema-clean payload a second
         time against PersonModel (strict schema). Failures add warnings only;
         they do not reject the result.
      7. Stats — attaches derivation metadata (person_id, github_username,
         source_repositories, contribution_ids, membership_ids) and LLM
         telemetry (model, provider, token counts) to AgentResult.

    Errors:
      LLMRuntimeError — raised on LLM call failures (config, network, or schema
                        rejection after pydantic-ai retries are exhausted).
      ValueError      — raised when context contains no usable identity signal
                        (no username, orcid, infoscience_id, or name).
    """

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
        """Generate and double-validate a person payload from gathered context.

        Args:
            context: Runtime context dict. Accepted identity keys (at least one
                required):
                - ``username`` / ``github_username`` (str): GitHub login.
                - ``orcid`` (str, optional): ORCID hint.
                - ``infoscience_id`` (str, optional): Infoscience person UUID.
                - ``name`` (str, optional): Display name hint for searching.

                Additional context keys:
                - ``contributions`` (list, optional): Contribution IDs.
                - ``source_repositories`` (list, optional): Repository handles.
                - ``uuid`` (str, optional): Stable UUID for this person.
                - ``agent_overrides`` (dict, optional): Field overrides applied
                  after the LLM call, before strict validation.
            providers: Injected provider bundle. infoscience and orcid providers
                       are used to build runtime tools when present.

        Returns:
            AgentResult with the validated person payload, merged warnings from
            both validation passes, raw LLM output, token counts, and derivation
            stats.
        """

        if _extract_person_identifier(context) is None:
            message = "Person context contains no usable identity signal (username, orcid, infoscience_id, or name)"
            raise ValueError(message)

        warnings: list[str] = []

        # Resolve GitHub username and optionally fetch profile.
        github_username: str | None = None
        github_user: dict[str, Any] = {}
        for key in ("username", "github_username"):
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                github_username = value.strip()
                break

        if github_username is not None:
            logger.info("%s — fetching GitHub profile", github_username)
            try:
                github_user = providers.github.get_user(github_username)
                logger.info("%s — GitHub profile fetched", github_username)
            except Exception as exc:  # noqa: BLE001
                # Transient GitHub API errors (502, rate limits) should not
                # abort the entire person extraction — proceed with an empty
                # profile and let the LLM use whatever other context is available.
                warnings.append(f"GitHub profile fetch failed for {github_username}: {exc}")
                logger.warning("%s — GitHub profile fetch failed: %s", github_username, exc)
                github_user = {"login": github_username}

        # Normalize ORCID hint from context or GitHub profile.
        orcid_hint = _normalize_orcid_hint(
            context.get("orcid") or github_user.get("orcid"),
        )

        # Stable UUID — reuse from context or generate fresh.
        uuid_value = context.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            uuid_value = generate_uuid()

        # Contribution and ownership lists.
        contributions: list[str] = []
        raw_contributions = context.get("contributions")
        if isinstance(raw_contributions, list):
            contributions = [c for c in raw_contributions if isinstance(c, str) and c]

        source_repositories: list[str] = []
        raw_source_repos = context.get("source_repositories")
        if isinstance(raw_source_repos, list):
            source_repositories = [r for r in raw_source_repos if isinstance(r, str) and r]

        # Build context payload for the LLM — include only non-empty values.
        llm_input: dict[str, Any] = {"uuid": uuid_value}
        if github_username:
            llm_input["username"] = github_username
        if github_user:
            llm_input["github_user"] = github_user
        if orcid_hint:
            llm_input["orcid_hint"] = orcid_hint
        raw_infoscience_id = context.get("infoscience_id")
        if isinstance(raw_infoscience_id, str) and raw_infoscience_id.strip():
            llm_input["infoscience_id"] = raw_infoscience_id.strip()
        raw_name = context.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            llm_input["name_hint"] = raw_name.strip()
        if contributions:
            llm_input["contributions"] = contributions
        if source_repositories:
            llm_input["source_repositories"] = source_repositories

        # Include repository context when available — README often contains
        # ORCID IDs, affiliations, and author credits that help the LLM
        # produce a richer person entity.
        raw_repo_ctx = context.get("repository_context")
        if isinstance(raw_repo_ctx, dict) and raw_repo_ctx:
            repo_meta = raw_repo_ctx.get("metadata")
            repo_readme = raw_repo_ctx.get("readme_content") or ""
            repo_gimie_jsonld = raw_repo_ctx.get("gimie_jsonld")
            repo_ctx_summary: dict[str, Any] = {}
            if isinstance(repo_meta, dict) and repo_meta:
                # Only include lightweight fields — skip nested contributor lists.
                repo_ctx_summary["name"] = repo_meta.get("name")
                repo_ctx_summary["description"] = repo_meta.get("description")
                repo_ctx_summary["full_name"] = repo_meta.get("full_name")
            if repo_readme:
                repo_ctx_summary["readme_content"] = repo_readme[:README_CONTEXT_MAX_CHARS]
            if isinstance(repo_gimie_jsonld, dict) and repo_gimie_jsonld:
                repo_ctx_summary["gimie_jsonld"] = json.dumps(
                    repo_gimie_jsonld, ensure_ascii=True
                )[:GIMIE_JSONLD_MAX_CHARS]
            if repo_ctx_summary:
                llm_input["repository_context"] = repo_ctx_summary

        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)

        # Build provider-dependent tools only when providers are available.
        tools = [fetch_link_content_via_selenium_tool, hash_user_email_tool]
        if providers.infoscience is not None:
            tools.append(make_infoscience_search_tool(providers.infoscience))
        if providers.orcid is not None:
            tools.append(make_orcid_person_tool(providers.orcid))

        identifier = (
            github_username
            or context.get("orcid")
            or context.get("infoscience_id")
            or context.get("name")
        )
        if not isinstance(identifier, str) or not identifier.strip():
            identifier = "unknown-person"
        logger.info("%s — calling LLM (%d tool(s) available)", identifier, len(tools))
        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=LLMPersonOutputShape,
                    tools=tools,
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            timeout_message = (
                f"{identifier} — LLM call timed out after {self._llm_call_timeout_seconds:.1f}s"
            )
            logger.error(timeout_message)
            raise LLMRuntimeError(timeout_message) from exc
        except LLMRuntimeError:
            raise
        except Exception as exc:
            raise LLMRuntimeError(str(exc)) from exc

        logger.info(
            "%s — LLM done (prompt=%s tokens, completion=%s tokens, requests=%s, tool_calls=%s)",
            identifier,
            llm_result.tokens_prompt,
            llm_result.tokens_completion,
            llm_result.requests,
            llm_result.tool_calls,
        )
        payload = {k: v for k, v in llm_result.payload.items() if v is not None}
        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        raw_output = deepcopy(payload)

        # Second validation pass: strict schema (warnings only, never raises).
        validation_warnings = warnings + _strict_validate(payload)

        derivation_stats = {
            "person_id": payload.get("id"),
            "github_username": github_username,
            "source_repositories": deepcopy(source_repositories),
            "contribution_ids": deepcopy(
                [c for c in (payload.get("pulse:hasContribution") or []) if isinstance(c, str)],
            ),
            "membership_ids": deepcopy(
                [m for m in (payload.get("org:hasMembership") or []) if isinstance(m, str)],
            ),
        }

        return AgentResult(
            data=payload,
            warnings=validation_warnings,
            raw_output=raw_output,
            model=llm_result.model,
            provider=llm_result.provider,
            tokens_prompt=llm_result.tokens_prompt,
            tokens_completion=llm_result.tokens_completion,
            stats={
                "agent_runtime": "llm",
                "derivation": derivation_stats,
            },
        )
