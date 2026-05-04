from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import date
from typing import Any

from pydantic import ValidationError

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm._payload_helpers import force_server_uuid
from src.v2.agents.llm.agent_tools.orcid_person import make_orcid_person_tool
from src.v2.agents.llm.agent_tools.query_orcid import make_query_orcid_tool
from src.v2.agents.llm.agent_tools.selenium_fetch import (
    make_fetch_link_content_tool,
)
from src.v2.agents.llm.prompt_context import append_runtime_prompt_context
from src.v2.agents.models import AgentResult, ProviderSet, generate_uuid
from src.v2.ingest.cache import ProviderCache
from src.v2.observation.query_log import stamp_current_agent
from src.v2.schema.models.agent import AgentMembershipShape
from src.v2.schema.models.strict import MembershipModel
from src.v2.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

MAX_CONTEXT_ENTITIES = 30

_PROMPTS_PACKAGE = "src.v2.agents.llm.membership.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")


def _resolve_membership_seed(context: dict[str, Any]) -> str:
    seed = context.get("membership_seed")
    if isinstance(seed, str) and seed.strip():
        return seed.strip()

    known_persons = context.get("known_persons")
    if isinstance(known_persons, list):
        for person in known_persons:
            if not isinstance(person, dict):
                continue
            person_id = person.get("id")
            if isinstance(person_id, str) and person_id.strip():
                return person_id.strip()

    message = "Membership context is missing membership_seed or known person id"
    raise ValueError(message)


def _strict_validate(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    try:
        MembershipModel.model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors():
            field = " -> ".join(str(loc) for loc in error["loc"])
            warnings.append(f"Strict schema warning at {field}: {error['msg']}")
    return warnings


def _list_of_dicts(value: Any, *, max_items: int = MAX_CONTEXT_ENTITIES) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    collected: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if len(collected) >= max_items:
            break
        collected.append(deepcopy(item))
    return collected


class LLMMembershipAgentV2:
    """LLM-backed agent that produces an org:Membership entity."""

    def __init__(
        self,
        *,
        llm_runtime: V2LLMRuntime | None = None,
        llm_call_timeout_seconds: float = 180.0,
        cache: ProviderCache | None = None,
    ) -> None:
        if llm_call_timeout_seconds <= 0:
            message = "llm_call_timeout_seconds must be > 0"
            raise ValueError(message)
        self._llm_runtime = llm_runtime or V2LLMRuntime()
        self._llm_call_timeout_seconds = float(llm_call_timeout_seconds)
        self._cache = cache

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        membership_seed = _resolve_membership_seed(context)

        stamp_current_agent(
            name="membership_agent",
            context={"membership_seed": membership_seed},
        )

        uuid_value = context.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            uuid_value = generate_uuid()

        llm_input: dict[str, Any] = {
            "membership_seed": membership_seed,
            "uuid": uuid_value,
            "detected_type": context.get("detected_type"),
            "source_url": context.get("source_url"),
            "known_persons": _list_of_dicts(context.get("known_persons")),
            "known_organizations": _list_of_dicts(context.get("known_organizations")),
            "person_derivations": _list_of_dicts(context.get("person_derivations")),
            "organization_derivations": _list_of_dicts(
                context.get("organization_derivations"),
            ),
            "typed_entity_buckets": context.get("typed_entity_buckets"),
        }

        target_person = context.get("target_person")
        if isinstance(target_person, dict) and target_person:
            llm_input["target_person"] = deepcopy(target_person)

        target_organizations = context.get("target_organizations")
        normalized_target_organizations = _list_of_dicts(target_organizations)
        if normalized_target_organizations:
            llm_input["target_organizations"] = normalized_target_organizations

        repository_context = context.get("repository_context")
        if isinstance(repository_context, dict) and repository_context:
            llm_input["repository_context"] = deepcopy(repository_context)

        pipeline_outputs = context.get("pipeline_outputs")
        if isinstance(pipeline_outputs, dict) and pipeline_outputs:
            llm_input["pipeline_outputs"] = pipeline_outputs

        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True, default=str)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)
        tools = [make_fetch_link_content_tool(self._cache)]
        if providers.orcid is not None:
            tools.append(make_orcid_person_tool(providers.orcid))
            tools.append(make_query_orcid_tool(providers.orcid))

        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=AgentMembershipShape,
                    tools=tools,
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            message = (
                f"{membership_seed} — LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            raise LLMRuntimeError(message) from exc
        except LLMRuntimeError:
            raise
        except Exception as exc:
            raise LLMRuntimeError(str(exc)) from exc

        payload = {key: value for key, value in llm_result.payload.items() if value is not None}
        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        force_server_uuid(payload, uuid_value)

        # Defend against inverted Membership dates: ORCID employment
        # records (and occasionally the LLM itself) emit `time:hasBeginning`
        # after `time:hasEnd`. The SHACL shape requires
        # `hasBeginning <= hasEnd`. Swap when both parse as ISO dates.
        beg = payload.get("time:hasBeginning")
        end = payload.get("time:hasEnd")
        if isinstance(beg, str) and isinstance(end, str):
            try:
                if date.fromisoformat(beg[:10]) > date.fromisoformat(end[:10]):
                    payload["time:hasBeginning"], payload["time:hasEnd"] = end, beg
            except ValueError:
                pass

        raw_output = deepcopy(payload)
        validation_warnings = _strict_validate(payload)

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
                "memberships": [deepcopy(payload)] if payload else [],
                "membership_count": 1 if payload else 0,
                "derivation": {
                    "membership_seed": membership_seed,
                    "membership_id": payload.get("id"),
                    "person_id": membership_seed,
                    "organization_id": payload.get("org:organization"),
                },
            },
        )
