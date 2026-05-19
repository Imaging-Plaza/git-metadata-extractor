from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

from pydantic import ValidationError

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm._payload_helpers import force_server_uuid
from src.v2.agents.llm.agent_tools.selenium_fetch import (
    make_fetch_link_content_tool,
)
from src.v2.agents.llm.prompt_context import append_runtime_prompt_context
from src.v2.agents.models import AgentResult, ProviderSet, generate_uuid
from src.v2.ingest.cache import ProviderCache
from src.v2.observation.query_log import stamp_current_agent
from src.v2.schema.models.agent import AgentContributionShape
from src.v2.schema.models.strict import ContributionModel
from src.v2.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

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


def _person_github_login(person: Any) -> str | None:
    """Best-effort GitHub login for a person entity.

    Prefers the explicit `pulse:githubUsername`; falls back to parsing
    `id` when shaped like `https://github.com/{login}`.
    """
    if not isinstance(person, dict):
        return None
    handle = person.get("pulse:githubUsername")
    if isinstance(handle, str) and handle.strip():
        return handle.strip()
    identifier = person.get("id")
    if isinstance(identifier, str) and identifier.startswith("https://github.com/"):
        candidate = identifier.removeprefix("https://github.com/").strip("/")
        if candidate and "/" not in candidate:
            return candidate
    return None


def _find_target_contributor_record(
    target_person: Any,
    target_repository: Any,
) -> dict[str, Any] | None:
    """Locate the contributor entry that matches `target_person` in
    `target_repository.contributors`.

    Returns the dict carrying the deterministic GitHub bookends
    (``firstContributionDate`` / ``lastContributionDate`` /
    ``contributions``) populated by ``gather_context``, or ``None``
    when no match is found.
    """
    if not isinstance(target_repository, dict):
        return None
    contributors = target_repository.get("contributors")
    if not isinstance(contributors, list) or not contributors:
        return None
    login = _person_github_login(target_person)
    if not login:
        return None
    target_login = login.casefold()
    for entry in contributors:
        if not isinstance(entry, dict):
            continue
        candidate = entry.get("login")
        if isinstance(candidate, str) and candidate.casefold() == target_login:
            return entry
    return None


class LLMContributionAgentV2:
    """LLM-backed agent that produces a pulse:Contribution entity."""

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
        del providers
        contribution_seed = _resolve_contribution_seed(context)

        stamp_current_agent(
            name="contribution_agent",
            context={"contribution_seed": contribution_seed},
        )

        uuid_value = context.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            uuid_value = generate_uuid()

        llm_input: dict[str, Any] = {
            "contribution_seed": contribution_seed,
            "uuid": uuid_value,
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
                    tools=[make_fetch_link_content_tool(self._cache)],
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

        # Authoritative pair: the orchestrator decided which (person, repo)
        # this invocation is for. The LLM is told the same in the system
        # prompt, but in practice it sometimes ignores `target_person` and
        # echoes whatever person was in `pipeline_outputs`. Force the
        # authoritative ids here so downstream dedup-by-id can't collapse
        # distinct contributions, regardless of LLM compliance.
        target_person = context.get("target_person")
        target_repository = context.get("target_repository")
        target_person_id = (
            target_person.get("id")
            if isinstance(target_person, dict)
            else None
        )
        target_repository_id = (
            target_repository.get("id")
            if isinstance(target_repository, dict)
            else None
        )

        # Fail closed: a Contribution without BOTH `schema:author` and
        # `pulse:contributionTo` violates pulse:ContributionShape (SHACL
        # mandates exactly one author + one contributionTo per node). When
        # either authoritative id is missing, do not emit the entity — a
        # malformed Contribution would otherwise leak past prune_dangling_refs
        # (its guard only catches *dangling* refs, not *missing* fields) and
        # land in the graph as an orphan.
        if not (
            isinstance(target_person_id, str)
            and target_person_id
            and isinstance(target_repository_id, str)
            and target_repository_id
        ):
            warning = (
                "llm_contribution: skipping Contribution emission — "
                f"target_person_id={target_person_id!r}, "
                f"target_repository_id={target_repository_id!r} "
                "(both required to satisfy pulse:ContributionShape)"
            )
            logger.warning(warning)
            return AgentResult(
                data={},
                warnings=[warning],
                raw_output={},
                model=llm_result.model,
                provider=llm_result.provider,
                tokens_prompt=llm_result.tokens_prompt,
                tokens_completion=llm_result.tokens_completion,
                stats={
                    "agent_runtime": "llm",
                    "contributions": [],
                    "contribution_count": 0,
                    "derivation": {
                        "contribution_seed": contribution_seed,
                        "skipped_reason": "missing_target_ids",
                        "target_person_id": target_person_id,
                        "target_repository_id": target_repository_id,
                    },
                },
            )

        payload["schema:author"] = target_person_id
        payload["pulse:contributionTo"] = target_repository_id
        # Double underscore separator — single `_` is ambiguous because
        # GitHub usernames are allowed to contain `_` (and URLs in the
        # repo id contain `/`), so `<person_url>_<repo_url>` cannot be
        # parsed back unambiguously. `__` never appears in either a
        # github.com URL or a ROR identifier, so the composite is
        # round-trippable. Migration: any previously persisted edges
        # with single-`_` separator are still readable by consumers —
        # only newly emitted edges adopt the new shape.
        composite_id = f"{target_person_id}__{target_repository_id}"
        payload["id"] = composite_id
        payload["idSource"] = "pulse:composite"
        identifiers = payload.get("identifiers")
        if not isinstance(identifiers, dict):
            identifiers = {}
            payload["identifiers"] = identifiers
        identifiers["pulse:composite"] = composite_id

        # Stamp count/first/last from the deterministic GitHub bookends
        # carried on `target_repository.contributors`. Without this, the
        # LLM tends to copy the lead contributor's stats onto every
        # Contribution (observed: cmdoret's 128 commits stamped on every
        # person in the repo, plus the repo-wide first/last commit dates
        # in place of per-person dates).
        contributor_record = _find_target_contributor_record(
            target_person, target_repository,
        )
        if contributor_record is not None:
            count = contributor_record.get("contributions")
            if isinstance(count, int) and count > 0:
                payload["pulse:contributionCount"] = count
            first_date = contributor_record.get("firstContributionDate")
            if isinstance(first_date, str) and first_date:
                payload["pulse:firstContributionDate"] = first_date
            last_date = contributor_record.get("lastContributionDate")
            if isinstance(last_date, str) and last_date:
                payload["pulse:lastContributionDate"] = last_date

        force_server_uuid(payload, uuid_value)

        # Drop empty edges. Production audit observed 28 contributions
        # landing with `pulse:contributionCount=0` and both date fields
        # `null` — vacuous edges that bloat the graph without carrying
        # any signal. They arise when the LLM emits a Contribution from
        # a weak signal (a comment, a watch, a review) but the GitHub
        # contributor record never produced a commit count or date.
        contribution_count = payload.get("pulse:contributionCount")
        first_contribution_date = payload.get("pulse:firstContributionDate")
        last_contribution_date = payload.get("pulse:lastContributionDate")
        if (
            (contribution_count is None or contribution_count == 0)
            and not first_contribution_date
            and not last_contribution_date
        ):
            warning = (
                f"llm_contribution: dropping Contribution {payload.get('id')!r} — "
                "no count, no firstContributionDate, no lastContributionDate"
            )
            logger.info(warning)
            return AgentResult(
                data={},
                warnings=[warning],
                raw_output={},
                model=llm_result.model,
                provider=llm_result.provider,
                tokens_prompt=llm_result.tokens_prompt,
                tokens_completion=llm_result.tokens_completion,
                stats={
                    "agent_runtime": "llm",
                    "contributions": [],
                    "contribution_count": 0,
                    "derivation": {
                        "contribution_seed": contribution_seed,
                        "skipped_reason": "empty_edge",
                    },
                },
            )

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
