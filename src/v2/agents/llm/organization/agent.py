from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.agent_tools.infoscience_orgunit import (
    make_infoscience_orgunit_tool,
)
from src.v2.agents.llm.agent_tools.ror_organization import (
    make_ror_organization_search_tool,
)
from src.v2.agents.llm.prompt_context import append_runtime_prompt_context
from src.v2.agents.models import AgentResult, ProviderSet, generate_uuid
from src.v2.generated.agent_entities import AgentOrganizationShape
from src.v2.generated.entities import OrganizationModel
from src.v2.llm.runtime import (
    LLMRuntimeError,
    V2LLMRuntime,
)

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "src.v2.agents.llm.organization.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")

README_CONTEXT_MAX_CHARS = 2000
GIMIE_JSONLD_MAX_CHARS = 4000


def _resolve_org_name(context: dict[str, Any]) -> str:
    for key in ("org_name", "organization", "github_organization_handle"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    message = "Organization context is missing a GitHub organization handle"
    raise ValueError(message)


def _strict_validate(payload: dict[str, Any]) -> list[str]:
    """Validate payload against strict OrganizationModel; return warnings only."""

    warnings: list[str] = []
    try:
        OrganizationModel.model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors():
            field = " -> ".join(str(loc) for loc in error["loc"])
            warnings.append(f"Strict schema warning at {field}: {error['msg']}")
    return warnings


class LLMOrganizationAgentV2:
    """LLM-backed agent that produces a pulse:OrganizationShape entity."""

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

    async def run(  # noqa: C901, PLR0912, PLR0915
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        org_name = _resolve_org_name(context)

        github_lookup_enabled = context.get("github_lookup_enabled")
        if not isinstance(github_lookup_enabled, bool):
            github_lookup_enabled = True

        uuid_value = context.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            uuid_value = generate_uuid()

        llm_input: dict[str, Any] = {
            "org_name": org_name,
            "uuid": uuid_value,
            "github_lookup_enabled": github_lookup_enabled,
        }

        source_url = context.get("source_url")
        if isinstance(source_url, str) and source_url:
            llm_input["source_url"] = source_url

        raw_source_repositories = context.get("source_repositories")
        source_repositories: list[str] = []
        if isinstance(raw_source_repositories, list):
            source_repositories = [
                repository
                for repository in raw_source_repositories
                if isinstance(repository, str) and repository
            ]
            if source_repositories:
                llm_input["source_repositories"] = source_repositories

        organization_context = context.get("organization_context")
        if isinstance(organization_context, dict) and organization_context:
            organization_context_summary: dict[str, Any] = {}
            profile = organization_context.get("profile")
            if isinstance(profile, dict) and profile:
                profile_summary = {
                    "login": profile.get("login"),
                    "name": profile.get("name"),
                    "description": profile.get("description"),
                    "followers": profile.get("followers"),
                    "type": profile.get("type"),
                }
                organization_context_summary["profile"] = {
                    key: value
                    for key, value in profile_summary.items()
                    if value is not None
                }
            for key in ("members", "owned_repos"):
                value = organization_context.get(key)
                if isinstance(value, list) and value:
                    organization_context_summary[key] = value
            if organization_context_summary:
                llm_input["organization_context"] = organization_context_summary

        repository_context = context.get("repository_context")
        if isinstance(repository_context, dict) and repository_context:
            repository_context_summary: dict[str, Any] = {}
            metadata = repository_context.get("metadata")
            if isinstance(metadata, dict) and metadata:
                metadata_summary = {
                    "name": metadata.get("name"),
                    "full_name": metadata.get("full_name"),
                    "description": metadata.get("description"),
                    "owner": metadata.get("owner"),
                }
                repository_context_summary["metadata"] = {
                    key: value
                    for key, value in metadata_summary.items()
                    if value is not None
                }
            readme_content = repository_context.get("readme_content")
            if isinstance(readme_content, str) and readme_content:
                repository_context_summary["readme_content"] = readme_content[
                    :README_CONTEXT_MAX_CHARS
                ]
            gimie_jsonld = repository_context.get("gimie_jsonld")
            if isinstance(gimie_jsonld, dict) and gimie_jsonld:
                repository_context_summary["gimie_jsonld"] = json.dumps(
                    gimie_jsonld,
                    ensure_ascii=True,
                )[:GIMIE_JSONLD_MAX_CHARS]
            if repository_context_summary:
                llm_input["repository_context"] = repository_context_summary

        pipeline_outputs = context.get("pipeline_outputs")
        if isinstance(pipeline_outputs, dict) and pipeline_outputs:
            llm_input["pipeline_outputs"] = pipeline_outputs

        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)

        tools = []
        if providers.ror is not None:
            tools.append(make_ror_organization_search_tool(providers.ror))
        if providers.infoscience is not None:
            tools.append(make_infoscience_orgunit_tool(providers.infoscience))

        identifier = org_name
        logger.info("%s — calling LLM (%d tool(s) available)", identifier, len(tools))
        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=AgentOrganizationShape,
                    tools=tools,
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            timeout_message = (
                f"{identifier} — LLM call timed out after {self._llm_call_timeout_seconds:.1f}s"
            )
            logger.exception(timeout_message)
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

        payload = {key: value for key, value in llm_result.payload.items() if value is not None}
        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        raw_output = deepcopy(payload)

        validation_warnings = _strict_validate(payload)

        derivation_stats = {
            "organization_id": payload.get("id"),
            "organization_name": payload.get("schema:name"),
            "github_lookup_enabled": github_lookup_enabled,
            "source_repositories": deepcopy(source_repositories),
            "owned_repositories": deepcopy(
                [
                    repository
                    for repository in payload.get("pulse:owns", [])
                    if isinstance(repository, str)
                ],
            )
            if isinstance(payload.get("pulse:owns"), list)
            else [],
            "parent_organization": payload.get("org:unitOf"),
            "unit_ids": deepcopy(
                [
                    unit_id
                    for unit_id in payload.get("org:hasUnit", [])
                    if isinstance(unit_id, str)
                ],
            )
            if isinstance(payload.get("org:hasUnit"), list)
            else [],
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
