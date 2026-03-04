from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.prompt_context import append_runtime_prompt_context
from src.v2.agents.models import AgentResult, ProviderSet
from src.v2.generated.agent_entities import AgentContributionShape
from src.v2.generated.entities import ContributionModel
from src.v2.llm.runtime import LLMRuntimeError, V2LLMRuntime

MAX_CONTEXT_ENTITIES = 30

_PROMPTS_PACKAGE = "src.v2.agents.llm.contribution.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")


def _resolve_contribution_seed(context: dict[str, Any]) -> str:
    seed = context.get("contribution_seed")
    if isinstance(seed, str) and seed.strip():
        return seed.strip()

    known_repositories = context.get("known_repositories")
    if isinstance(known_repositories, list):
        for repository in known_repositories:
            if not isinstance(repository, dict):
                continue
            for key in ("id", "pulse:githubRepositoryHandle", "full_name"):
                value = repository.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

    message = "Contribution context is missing contribution_seed or repository id"
    raise ValueError(message)


def _strict_validate(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    try:
        ContributionModel.model_validate(payload)
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


class LLMContributionAgentV2:
    """LLM-backed agent that produces a pulse:Contribution entity."""

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
        del providers
        contribution_seed = _resolve_contribution_seed(context)

        llm_input: dict[str, Any] = {
            "contribution_seed": contribution_seed,
            "detected_type": context.get("detected_type"),
            "source_url": context.get("source_url"),
            "known_persons": _list_of_dicts(context.get("known_persons")),
            "known_repositories": _list_of_dicts(context.get("known_repositories")),
            "known_organizations": _list_of_dicts(context.get("known_organizations")),
            "person_derivations": _list_of_dicts(context.get("person_derivations")),
            "repository_derivations": _list_of_dicts(
                context.get("repository_derivations"),
            ),
            "typed_entity_buckets": context.get("typed_entity_buckets"),
        }

        repository_context = context.get("repository_context")
        if isinstance(repository_context, dict) and repository_context:
            llm_input["repository_context"] = deepcopy(repository_context)

        pipeline_outputs = context.get("pipeline_outputs")
        if isinstance(pipeline_outputs, dict) and pipeline_outputs:
            llm_input["pipeline_outputs"] = pipeline_outputs

        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True, default=str)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)

        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=AgentContributionShape,
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            message = (
                f"{contribution_seed} — LLM call timed out after "
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
                "contributions": [deepcopy(payload)] if payload else [],
                "contribution_count": 1 if payload else 0,
                "derivation": {
                    "contribution_seed": contribution_seed,
                    "contribution_id": payload.get("id"),
                    "person_id": payload.get("schema:author"),
                    "repository_id": payload.get("pulse:contributionTo"),
                    "count": payload.get("pulse:contributionCount"),
                },
            },
        )
