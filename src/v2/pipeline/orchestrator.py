from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Awaitable, Callable

from src.v2.agents import (
    ArticleAgentV2,
    ContributionAgentV2,
    MembershipAgentV2,
    OrganizationAgentV2,
    PersonAgentV2,
    ProviderSet,
    RepositoryAgentV2,
    TypedEntityBuckets,
    infer_entity_bucket,
    with_retry,
)
from src.v2.agents.models import AgentResult
from src.v2.detection.models import GitHubURLClassification
from src.v2.observability.agent_instrumentation import instrument_agent
from src.v2.observability.pipeline_spans import PipelineTracer
from src.v2.pipeline.models import AgentGroup, ExecutionPlan, PipelineResult, Stage
from src.v2.pipeline.stages import ContextBundle, gather_context

ContextGatherer = Callable[
    [str, GitHubURLClassification, ProviderSet],
    ContextBundle | Awaitable[ContextBundle],
]
AgentRunner = Callable[
    [dict[str, Any], ProviderSet],
    AgentResult | Awaitable[AgentResult] | dict[str, Any] | Awaitable[dict[str, Any]],
]
SleepCallable = Callable[[float], Awaitable[None]]

STAGE_CONTEXT_GATHER = "context_gather"
STAGE_REPO_AGENT = "repo_agent"
STAGE_PERSON_AGENT = "person_agent"
STAGE_ORG_AGENT = "org_agent"
STAGE_ARTICLE_AGENT = "article_agent"
STAGE_MEMBERSHIP_AGENT = "membership_agent"
STAGE_CONTRIBUTION_AGENT = "contribution_agent"
STAGE_PERSON_AGENTS = "person_agents"
STAGE_REPO_AGENTS = "repo_agents"
STAGE_ORG_AGENTS = "org_agents"
STAGE_ARTICLE_AGENTS = "article_agents"
STAGE_MEMBERSHIP_AGENTS = "membership_agents"
STAGE_CONTRIBUTION_AGENTS = "contribution_agents"
STAGE_AGENTS = "agents"
GITHUB_LOGIN_PATTERN = re.compile(r"^[A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d])){0,38}$")
VALIDATION_WARNING_PREFIX = "Validation warning at"

PLAN_BY_TYPE: dict[str, list[str]] = {
    "repository": [
        STAGE_CONTEXT_GATHER,
        STAGE_REPO_AGENT,
        STAGE_PERSON_AGENTS,
        STAGE_ORG_AGENTS,
        STAGE_ARTICLE_AGENTS,
        STAGE_MEMBERSHIP_AGENTS,
        STAGE_CONTRIBUTION_AGENTS,
    ],
    "user": [
        STAGE_CONTEXT_GATHER,
        STAGE_PERSON_AGENT,
        STAGE_REPO_AGENTS,
        STAGE_ORG_AGENTS,
        STAGE_ARTICLE_AGENTS,
        STAGE_MEMBERSHIP_AGENTS,
        STAGE_CONTRIBUTION_AGENTS,
    ],
    "organization": [
        STAGE_CONTEXT_GATHER,
        STAGE_ORG_AGENT,
        STAGE_PERSON_AGENTS,
        STAGE_REPO_AGENTS,
        STAGE_ARTICLE_AGENTS,
        STAGE_MEMBERSHIP_AGENTS,
        STAGE_CONTRIBUTION_AGENTS,
    ],
}


@dataclass(slots=True)
class _StageWorkItem:
    result_key: str
    runner_key: str
    context: dict[str, Any]


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _append_unique(items: list[str], message: str) -> None:
    if message and message not in items:
        items.append(message)


def _deduplicate(values: list[str]) -> list[str]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        deduplicated.append(value)
        seen.add(value)
    return deduplicated


def _to_detected_type(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _is_valid_github_login(candidate: Any) -> bool:
    return isinstance(candidate, str) and bool(
        GITHUB_LOGIN_PATTERN.fullmatch(candidate.strip()),
    )


class PipelineOrchestrator:
    def __init__(  # noqa: PLR0913
        self,
        *,
        context_gatherer: ContextGatherer = gather_context,
        repository_agent: RepositoryAgentV2 | None = None,
        person_agent: PersonAgentV2 | None = None,
        organization_agent: OrganizationAgentV2 | None = None,
        article_agent: ArticleAgentV2 | None = None,
        membership_agent: MembershipAgentV2 | None = None,
        contribution_agent: ContributionAgentV2 | None = None,
        agent_runners: dict[str, AgentRunner] | None = None,
        retry_max_retries: int = 3,
        retry_backoff_base: float = 0.0,
        retry_sleep_func: SleepCallable | None = None,
    ) -> None:
        self._context_gatherer = context_gatherer
        self._repository_agent = repository_agent or RepositoryAgentV2()
        self._person_agent = person_agent or PersonAgentV2()
        self._organization_agent = organization_agent or OrganizationAgentV2()
        self._article_agent = article_agent or ArticleAgentV2()
        self._membership_agent = membership_agent or MembershipAgentV2()
        self._contribution_agent = contribution_agent or ContributionAgentV2()

        self._agent_runners: dict[str, AgentRunner] = {
            STAGE_REPO_AGENT: self._repository_agent.run,
            STAGE_PERSON_AGENT: self._person_agent.run,
            STAGE_ORG_AGENT: self._organization_agent.run,
            STAGE_ARTICLE_AGENT: self._article_agent.run,
            STAGE_MEMBERSHIP_AGENT: self._membership_agent.run,
            STAGE_CONTRIBUTION_AGENT: self._contribution_agent.run,
        }
        if agent_runners:
            self._agent_runners.update(agent_runners)

        self._retry_max_retries = retry_max_retries
        self._retry_backoff_base = retry_backoff_base
        self._retry_sleep_func = retry_sleep_func

    def get_execution_plan(self, detected_type: str) -> ExecutionPlan:
        normalized_type = _to_detected_type(detected_type)
        stage_names = PLAN_BY_TYPE.get(normalized_type)
        if stage_names is None:
            raise ValueError

        stages: list[Stage] = []
        for index, stage_name in enumerate(stage_names):
            depends_on = [stage_names[index - 1]] if index > 0 else []
            stages.append(
                Stage(
                    name=stage_name,
                    groups=[self._build_group(stage_name)],
                    depends_on=depends_on,
                ),
            )

        plan = ExecutionPlan(detected_type=normalized_type, stages=stages)
        if plan.has_circular_dependencies():
            raise ValueError
        return plan

    async def execute(  # noqa: C901
        self,
        plan: ExecutionPlan,
        providers: ProviderSet,
        context: dict[str, Any],
    ) -> PipelineResult:
        started_at = perf_counter()
        stages_completed: list[str] = []
        warnings: list[str] = []
        errors: list[str] = []
        agent_results: dict[str, AgentResult] = {}

        runtime_context = dict(context)
        run_id = runtime_context.get("run_id") if isinstance(runtime_context.get("run_id"), str) else None
        pipeline_tracer = runtime_context.get("pipeline_tracer")
        if not isinstance(pipeline_tracer, PipelineTracer):
            pipeline_tracer = PipelineTracer(run_id=run_id)
        runtime_context["pipeline_tracer"] = pipeline_tracer
        runtime_context["run_id"] = run_id
        pipeline_outputs: dict[str, dict[str, Any]] = {}
        pipeline_agent_results: dict[str, AgentResult] = {}
        runtime_context["pipeline_agent_results"] = dict(pipeline_agent_results)

        for stage in plan.stages:
            if stage.name == STAGE_CONTEXT_GATHER:
                with pipeline_tracer.trace_stage(
                    STAGE_CONTEXT_GATHER,
                    detected_type=plan.detected_type,
                ) as stage_span:
                    url_info = self._require_url_info(runtime_context)
                    context_bundle = await _maybe_await(
                        self._context_gatherer(plan.detected_type, url_info, providers),
                    )
                    runtime_context["context_bundle"] = context_bundle
                    runtime_context["gathered_context"] = context_bundle.context
                    runtime_context["pipeline_outputs"] = dict(pipeline_outputs)
                    for warning in context_bundle.warnings:
                        _append_unique(warnings, warning)
                    stage_span.set_attributes(
                        warning_count=len(context_bundle.warnings),
                        context_keys=sorted(context_bundle.context.keys()),
                    )
                stages_completed.append(stage.name)
                continue

            work_items = self._build_work_items(stage.name, plan.detected_type, runtime_context)
            if stage.name == STAGE_PERSON_AGENTS and work_items:
                work_items, pre_stage_warnings = self._filter_person_work_items(
                    work_items,
                    providers,
                )
                for warning in pre_stage_warnings:
                    _append_unique(warnings, warning)
            with pipeline_tracer.trace_stage(
                STAGE_AGENTS,
                orchestrator_stage=stage.name,
                work_item_count=len(work_items),
            ) as stage_span:
                if not work_items:
                    stage_span.set_attribute("status", "skipped")
                    stages_completed.append(stage.name)
                    continue

                stage_results, stage_warnings, stage_errors = await self._execute_stage(
                    stage_name=stage.name,
                    work_items=work_items,
                    runtime_context=runtime_context,
                    pipeline_outputs=pipeline_outputs,
                    providers=providers,
                )
                stage_span.set_attributes(
                    result_count=len(stage_results),
                    warning_count=len(stage_warnings),
                    error_count=len(stage_errors),
                )

            for key, value in stage_results.items():
                agent_results[key] = value
                pipeline_outputs[key] = value.data
                pipeline_agent_results[key] = value
                runtime_context["pipeline_outputs"] = dict(pipeline_outputs)
                runtime_context["pipeline_agent_results"] = dict(pipeline_agent_results)

            for warning in stage_warnings:
                _append_unique(warnings, warning)
            for error in stage_errors:
                _append_unique(errors, error)

            stages_completed.append(stage.name)

        duration_ms = int((perf_counter() - started_at) * 1000)
        return PipelineResult(
            stages_completed=stages_completed,
            agent_results=agent_results,
            warnings=warnings,
            errors=errors,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _build_group(stage_name: str) -> AgentGroup:  # noqa: PLR0911
        if stage_name in {
            STAGE_CONTEXT_GATHER,
            STAGE_REPO_AGENT,
            STAGE_PERSON_AGENT,
            STAGE_ORG_AGENT,
        }:
            return AgentGroup(
                name=stage_name,
                agent_keys=[stage_name],
                parallelizable=False,
            )
        if stage_name == STAGE_PERSON_AGENTS:
            return AgentGroup(
                name=stage_name,
                agent_keys=[STAGE_PERSON_AGENT],
                parallelizable=True,
            )
        if stage_name == STAGE_REPO_AGENTS:
            return AgentGroup(
                name=stage_name,
                agent_keys=[STAGE_REPO_AGENT],
                parallelizable=True,
            )
        if stage_name == STAGE_ORG_AGENTS:
            return AgentGroup(
                name=stage_name,
                agent_keys=[STAGE_ORG_AGENT],
                parallelizable=True,
            )
        if stage_name == STAGE_ARTICLE_AGENTS:
            return AgentGroup(
                name=stage_name,
                agent_keys=[STAGE_ARTICLE_AGENT],
                parallelizable=True,
            )
        if stage_name == STAGE_MEMBERSHIP_AGENTS:
            return AgentGroup(
                name=stage_name,
                agent_keys=[STAGE_MEMBERSHIP_AGENT],
                parallelizable=True,
            )
        if stage_name == STAGE_CONTRIBUTION_AGENTS:
            return AgentGroup(
                name=stage_name,
                agent_keys=[STAGE_CONTRIBUTION_AGENT],
                parallelizable=True,
            )
        return AgentGroup(name=stage_name, agent_keys=[], parallelizable=False)

    @staticmethod
    def _require_url_info(context: dict[str, Any]) -> GitHubURLClassification:
        url_info = context.get("url_info")
        if isinstance(url_info, GitHubURLClassification):
            return url_info
        raise ValueError

    async def _execute_stage(
        self,
        *,
        stage_name: str,
        work_items: list[_StageWorkItem],
        runtime_context: dict[str, Any],
        pipeline_outputs: dict[str, dict[str, Any]],
        providers: ProviderSet,
    ) -> tuple[dict[str, AgentResult], list[str], list[str]]:
        stage_warnings: list[str] = []
        stage_errors: list[str] = []
        stage_results: dict[str, AgentResult] = {}

        async def _run_item(work_item: _StageWorkItem) -> tuple[str, AgentResult]:
            runner = self._agent_runners.get(work_item.runner_key)
            if runner is None:
                raise ValueError

            item_context = {
                **runtime_context,
                **work_item.context,
                "stage_name": stage_name,
                "pipeline_outputs": dict(pipeline_outputs),
            }

            async def _run_with_retry(
                agent_context: dict[str, Any],
                agent_providers: ProviderSet,
            ) -> AgentResult:
                invalid_result_checker = self._invalid_result_checker_for_runner(
                    work_item.runner_key,
                )
                return await with_retry(
                    runner,
                    context=agent_context,
                    providers=agent_providers,
                    max_retries=self._retry_max_retries,
                    backoff_base=self._retry_backoff_base,
                    sleep_func=self._retry_sleep_func,
                    invalid_result_checker=invalid_result_checker,
                )

            run_id = runtime_context.get("run_id")
            traced_runner = instrument_agent(
                _run_with_retry,
                run_id if isinstance(run_id, str) else None,
                agent_name=work_item.result_key,
                model=item_context.get("model")
                if isinstance(item_context.get("model"), str)
                else None,
                provider=item_context.get("provider")
                if isinstance(item_context.get("provider"), str)
                else None,
            )
            result = await _maybe_await(traced_runner(item_context, providers))
            return work_item.result_key, result

        gathered = await asyncio.gather(
            *(_run_item(item) for item in work_items),
            return_exceptions=True,
        )

        for index, result in enumerate(gathered):
            work_item = work_items[index]
            if isinstance(result, BaseException):
                message = f"{work_item.result_key} execution failed: {result}"
                _append_unique(stage_errors, message)
                stage_results[work_item.result_key] = AgentResult(
                    data={},
                    warnings=[message],
                    raw_output={},
                    is_partial=True,
                    failure_reason=str(result),
                    stats={"attempts": 1, "retry_count": 0, "backoff_schedule": []},
                )
                continue

            result_key, agent_result = result
            stage_results[result_key] = agent_result
            for warning in agent_result.warnings:
                _append_unique(stage_warnings, f"{result_key}: {warning}")
            if agent_result.is_partial and agent_result.failure_reason:
                _append_unique(
                    stage_errors,
                    f"{result_key}: {agent_result.failure_reason}",
                )

        return stage_results, stage_warnings, stage_errors

    @staticmethod
    def _invalid_result_checker_for_runner(
        runner_key: str,
    ) -> Callable[[AgentResult], str | None] | None:
        if runner_key not in {
            STAGE_ARTICLE_AGENT,
            STAGE_MEMBERSHIP_AGENT,
            STAGE_CONTRIBUTION_AGENT,
        }:
            return None

        def _class_stage_checker(result: AgentResult) -> str | None:
            if result.is_partial:
                return result.failure_reason or "Agent returned partial result"

            for warning in result.warnings:
                if warning.startswith(VALIDATION_WARNING_PREFIX):
                    return "Agent output did not satisfy permissive validation"

            if not isinstance(result.data, dict):
                return "Agent returned invalid data payload"

            # Empty payloads are valid for class-stage fanout agents.
            return None

        return _class_stage_checker

    def _build_work_items(  # noqa: PLR0911
        self,
        stage_name: str,
        detected_type: str,
        runtime_context: dict[str, Any],
    ) -> list[_StageWorkItem]:
        normalized_type = _to_detected_type(detected_type)
        if stage_name == STAGE_REPO_AGENT and normalized_type == "repository":
            return [
                _StageWorkItem(
                    result_key=STAGE_REPO_AGENT,
                    runner_key=STAGE_REPO_AGENT,
                    context=self._repository_root_context(runtime_context),
                ),
            ]

        if stage_name == STAGE_PERSON_AGENT and normalized_type == "user":
            return [
                _StageWorkItem(
                    result_key=STAGE_PERSON_AGENT,
                    runner_key=STAGE_PERSON_AGENT,
                    context=self._person_root_context(runtime_context),
                ),
            ]

        if stage_name == STAGE_ORG_AGENT and normalized_type == "organization":
            return [
                _StageWorkItem(
                    result_key=STAGE_ORG_AGENT,
                    runner_key=STAGE_ORG_AGENT,
                    context=self._organization_root_context(runtime_context),
                ),
            ]

        if stage_name == STAGE_PERSON_AGENTS:
            person_contexts = self._person_fanout_contexts(runtime_context, normalized_type)
            return [
                _StageWorkItem(
                    result_key=f"{STAGE_PERSON_AGENT}:{context['username']}",
                    runner_key=STAGE_PERSON_AGENT,
                    context=context,
                )
                for context in person_contexts
            ]

        if stage_name == STAGE_REPO_AGENTS:
            repo_contexts = self._repository_fanout_contexts(runtime_context, normalized_type)
            return [
                _StageWorkItem(
                    result_key=f"{STAGE_REPO_AGENT}:{context['full_name']}",
                    runner_key=STAGE_REPO_AGENT,
                    context=context,
                )
                for context in repo_contexts
            ]

        if stage_name == STAGE_ORG_AGENTS:
            org_contexts = self._organization_fanout_contexts(runtime_context, normalized_type)
            return [
                _StageWorkItem(
                    result_key=f"{STAGE_ORG_AGENT}:{context['org_name']}",
                    runner_key=STAGE_ORG_AGENT,
                    context=context,
                )
                for context in org_contexts
            ]

        if stage_name == STAGE_ARTICLE_AGENTS:
            article_contexts = self._article_fanout_contexts(runtime_context, normalized_type)
            return [
                _StageWorkItem(
                    result_key=f"{STAGE_ARTICLE_AGENT}:{context['article_seed']}",
                    runner_key=STAGE_ARTICLE_AGENT,
                    context=context,
                )
                for context in article_contexts
            ]

        if stage_name == STAGE_MEMBERSHIP_AGENTS:
            membership_contexts = self._membership_fanout_contexts(
                runtime_context,
                normalized_type,
            )
            return [
                _StageWorkItem(
                    result_key=f"{STAGE_MEMBERSHIP_AGENT}:{context['membership_seed']}",
                    runner_key=STAGE_MEMBERSHIP_AGENT,
                    context=context,
                )
                for context in membership_contexts
            ]

        if stage_name == STAGE_CONTRIBUTION_AGENTS:
            contribution_contexts = self._contribution_fanout_contexts(
                runtime_context,
                normalized_type,
            )
            return [
                _StageWorkItem(
                    result_key=f"{STAGE_CONTRIBUTION_AGENT}:{context['contribution_seed']}",
                    runner_key=STAGE_CONTRIBUTION_AGENT,
                    context=context,
                )
                for context in contribution_contexts
            ]

        return []

    @staticmethod
    def _filter_person_work_items(
        work_items: list[_StageWorkItem],
        providers: ProviderSet,
    ) -> tuple[list[_StageWorkItem], list[str]]:
        filtered_items: list[_StageWorkItem] = []
        warnings: list[str] = []
        account_type_by_username: dict[str, str | None] = {}

        for work_item in work_items:
            username = work_item.context.get("username")
            if not isinstance(username, str) or not username:
                filtered_items.append(work_item)
                continue

            if username not in account_type_by_username:
                account_type: str | None = None
                try:
                    github_user = providers.github.get_user(username)
                except Exception:  # noqa: BLE001
                    github_user = {}
                if isinstance(github_user, dict):
                    raw_type = github_user.get("type")
                    if isinstance(raw_type, str) and raw_type:
                        account_type = raw_type.lower()
                account_type_by_username[username] = account_type

            if account_type_by_username.get(username) == "organization":
                _append_unique(
                    warnings,
                    (
                        "Skipping person fanout for GitHub organization account: "
                        f"{username}"
                    ),
                )
                continue

            filtered_items.append(work_item)

        return filtered_items, warnings

    def _repository_root_context(self, runtime_context: dict[str, Any]) -> dict[str, Any]:
        bundle = runtime_context.get("context_bundle")
        repository_context = (
            bundle.context.get("repository", {})
            if isinstance(bundle, ContextBundle)
            else {}
        )
        full_name = repository_context.get("full_name")
        if not isinstance(full_name, str) or "/" not in full_name:
            url_info = self._require_url_info(runtime_context)
            full_name = f"{url_info.owner}/{url_info.repo or ''}".rstrip("/")
        return {
            "full_name": full_name,
            "source_url": runtime_context.get("source_url"),
            "repository_context": repository_context,
        }

    def _person_root_context(self, runtime_context: dict[str, Any]) -> dict[str, Any]:
        bundle = runtime_context.get("context_bundle")
        user_context = bundle.context.get("user", {}) if isinstance(bundle, ContextBundle) else {}
        return {
            "username": user_context.get("username") or self._require_url_info(runtime_context).owner,
            "source_url": runtime_context.get("source_url"),
            "user_context": user_context,
        }

    def _organization_root_context(self, runtime_context: dict[str, Any]) -> dict[str, Any]:
        bundle = runtime_context.get("context_bundle")
        organization_context = (
            bundle.context.get("organization", {})
            if isinstance(bundle, ContextBundle)
            else {}
        )
        return {
            "org_name": organization_context.get("org_name") or self._require_url_info(runtime_context).owner,
            "source_url": runtime_context.get("source_url"),
            "organization_context": organization_context,
        }

    def _repository_source_full_name(self, runtime_context: dict[str, Any]) -> str | None:
        bundle = runtime_context.get("context_bundle")
        repository_context = (
            bundle.context.get("repository", {})
            if isinstance(bundle, ContextBundle)
            else {}
        )
        full_name = repository_context.get("full_name")
        if isinstance(full_name, str) and "/" in full_name:
            return full_name

        url_info = self._require_url_info(runtime_context)
        if isinstance(url_info.repo, str) and url_info.repo:
            return f"{url_info.owner}/{url_info.repo}"
        return None

    def _repository_source_repositories(
        self,
        runtime_context: dict[str, Any],
    ) -> list[str]:
        full_name = self._repository_source_full_name(runtime_context)
        return [full_name] if isinstance(full_name, str) and full_name else []

    def _person_fanout_contexts(  # noqa: C901
        self,
        runtime_context: dict[str, Any],
        detected_type: str,
    ) -> list[dict[str, Any]]:
        bundle = runtime_context.get("context_bundle")
        if not isinstance(bundle, ContextBundle):
            return []

        usernames: list[str] = []
        if detected_type == "repository":
            repository_context = bundle.context.get("repository", {})
            contributors = repository_context.get("contributors", [])
            if isinstance(contributors, list):
                for contributor in contributors:
                    if isinstance(contributor, dict):
                        login = contributor.get("login")
                        if _is_valid_github_login(login):
                            usernames.append(str(login).strip())
                    elif isinstance(contributor, str) and contributor:
                        if _is_valid_github_login(contributor):
                            usernames.append(contributor.strip())
        if detected_type == "organization":
            organization_context = bundle.context.get("organization", {})
            members = organization_context.get("members", [])
            if isinstance(members, list):
                usernames.extend(
                    [member for member in members if isinstance(member, str) and member],
                )

        source_repositories = (
            self._repository_source_repositories(runtime_context)
            if detected_type == "repository"
            else []
        )
        contexts: list[dict[str, Any]] = []
        for username in _deduplicate(usernames):
            context: dict[str, Any] = {
                "username": username,
                "source_url": runtime_context.get("source_url"),
            }
            if source_repositories:
                context["source_repositories"] = list(source_repositories)
            contexts.append(context)
        return contexts

    def _repository_fanout_contexts(
        self,
        runtime_context: dict[str, Any],
        detected_type: str,
    ) -> list[dict[str, Any]]:
        bundle = runtime_context.get("context_bundle")
        if not isinstance(bundle, ContextBundle):
            return []

        owner = self._require_url_info(runtime_context).owner
        full_names: list[str] = []
        if detected_type == "user":
            user_context = bundle.context.get("user", {})
            repos = user_context.get("owned_repos", [])
            if isinstance(repos, list):
                for repo in repos:
                    if not isinstance(repo, str) or not repo:
                        continue
                    full_names.append(repo if "/" in repo else f"{owner}/{repo}")
        if detected_type == "organization":
            organization_context = bundle.context.get("organization", {})
            repos = organization_context.get("owned_repos", [])
            if isinstance(repos, list):
                for repo in repos:
                    if not isinstance(repo, str) or not repo:
                        continue
                    full_names.append(repo if "/" in repo else f"{owner}/{repo}")

        return [
            {"full_name": full_name, "source_url": runtime_context.get("source_url")}
            for full_name in _deduplicate(full_names)
        ]

    def _organization_fanout_contexts(  # noqa: C901, PLR0912
        self,
        runtime_context: dict[str, Any],
        detected_type: str,
    ) -> list[dict[str, Any]]:
        bundle = runtime_context.get("context_bundle")
        if not isinstance(bundle, ContextBundle):
            return []

        source_url = runtime_context.get("source_url")
        source_repositories = (
            self._repository_source_repositories(runtime_context)
            if detected_type == "repository"
            else []
        )
        contexts_by_org_name: dict[str, dict[str, Any]] = {}

        def _set_context(
            org_name: str,
            *,
            github_lookup_enabled: bool,
            include_source_repositories: bool = False,
        ) -> None:
            if not org_name:
                return
            existing_context = contexts_by_org_name.get(org_name)
            if existing_context is not None:
                existing_lookup = existing_context.get("github_lookup_enabled")
                if github_lookup_enabled and existing_lookup is False:
                    existing_context["github_lookup_enabled"] = True
                    if include_source_repositories and source_repositories:
                        existing_context["source_repositories"] = list(
                            source_repositories,
                        )
                return

            next_context: dict[str, Any] = {
                "org_name": org_name,
                "source_url": source_url,
                "github_lookup_enabled": github_lookup_enabled,
            }
            if include_source_repositories and source_repositories:
                next_context["source_repositories"] = list(source_repositories)
            contexts_by_org_name[org_name] = next_context

        if detected_type == "repository":
            repository_context = bundle.context.get("repository", {})
            repository_metadata = repository_context.get("metadata", {})
            if isinstance(repository_metadata, dict):
                owner = repository_metadata.get("owner")
                if isinstance(owner, dict):
                    owner_login = owner.get("login")
                    owner_type = str(owner.get("type", "")).lower()
                    if isinstance(owner_login, str) and "organization" in owner_type:
                        _set_context(
                            owner_login,
                            github_lookup_enabled=True,
                            include_source_repositories=True,
                        )

        if detected_type == "user":
            user_context = bundle.context.get("user", {})
            profile = user_context.get("profile", {})
            if isinstance(profile, dict):
                company = profile.get("company")
                if isinstance(company, str) and company:
                    _set_context(company, github_lookup_enabled=True)

        pipeline_outputs = runtime_context.get("pipeline_outputs", {})
        if isinstance(pipeline_outputs, dict):
            for result_key, payload in pipeline_outputs.items():
                if not str(result_key).startswith(STAGE_PERSON_AGENT):
                    continue
                memberships = payload.get("org:hasMembership") if isinstance(payload, dict) else None
                if not isinstance(memberships, list):
                    continue
                for membership in memberships:
                    if not isinstance(membership, str) or "_" not in membership:
                        continue
                    _, organization = membership.split("_", maxsplit=1)
                    if organization:
                        _set_context(
                            organization,
                            github_lookup_enabled=detected_type != "repository",
                        )

        return list(contexts_by_org_name.values())

    @staticmethod
    def _collect_stage_payloads(
        runtime_context: dict[str, Any],
        stage_prefix: str,
    ) -> list[dict[str, Any]]:
        pipeline_outputs = runtime_context.get("pipeline_outputs")
        if not isinstance(pipeline_outputs, dict):
            return []

        payloads: list[dict[str, Any]] = []
        for result_key in sorted(pipeline_outputs):
            if not str(result_key).startswith(stage_prefix):
                continue
            payload = pipeline_outputs[result_key]
            if isinstance(payload, dict) and payload:
                payloads.append(payload)
        return payloads

    @staticmethod
    def _collect_stage_derivations(
        runtime_context: dict[str, Any],
        stage_prefix: str,
    ) -> list[dict[str, Any]]:
        pipeline_agent_results = runtime_context.get("pipeline_agent_results")
        if not isinstance(pipeline_agent_results, dict):
            return []

        derivations: list[dict[str, Any]] = []
        for result_key in sorted(pipeline_agent_results):
            if not str(result_key).startswith(stage_prefix):
                continue
            result = pipeline_agent_results[result_key]
            if not isinstance(result, AgentResult):
                continue
            stats = result.stats
            if not isinstance(stats, dict):
                continue
            derivation = stats.get("derivation")
            if isinstance(derivation, dict):
                derivations.append(derivation)
        return derivations

    @staticmethod
    def _typed_entity_bucket_snapshot(
        runtime_context: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        buckets = TypedEntityBuckets()
        pipeline_outputs = runtime_context.get("pipeline_outputs")
        if not isinstance(pipeline_outputs, dict):
            return buckets.to_dict()

        for result_key in sorted(pipeline_outputs):
            payload = pipeline_outputs[result_key]
            if not isinstance(payload, dict) or not payload:
                continue
            bucket_name = infer_entity_bucket(agent_key=str(result_key), data=payload)
            if bucket_name is None:
                continue
            buckets.add(bucket_name, payload)

        return buckets.to_dict()

    def _class_agent_base_context(
        self,
        runtime_context: dict[str, Any],
        detected_type: str,
    ) -> dict[str, Any]:
        known_persons = self._collect_stage_payloads(runtime_context, STAGE_PERSON_AGENT)
        known_organizations = self._collect_stage_payloads(runtime_context, STAGE_ORG_AGENT)
        known_repositories = self._collect_stage_payloads(runtime_context, STAGE_REPO_AGENT)

        base_context: dict[str, Any] = {
            "detected_type": detected_type,
            "source_url": runtime_context.get("source_url"),
            "known_persons": deepcopy(known_persons),
            "known_organizations": deepcopy(known_organizations),
            "known_repositories": deepcopy(known_repositories),
            "person_derivations": self._collect_stage_derivations(
                runtime_context,
                STAGE_PERSON_AGENT,
            ),
            "organization_derivations": self._collect_stage_derivations(
                runtime_context,
                STAGE_ORG_AGENT,
            ),
            "repository_derivations": self._collect_stage_derivations(
                runtime_context,
                STAGE_REPO_AGENT,
            ),
            "typed_entity_buckets": self._typed_entity_bucket_snapshot(runtime_context),
        }

        if detected_type == "repository":
            full_name = self._repository_source_full_name(runtime_context)
            if isinstance(full_name, str) and full_name:
                base_context["full_name"] = full_name
            bundle = runtime_context.get("context_bundle")
            if isinstance(bundle, ContextBundle):
                repository_context = bundle.context.get("repository")
                if isinstance(repository_context, dict):
                    base_context["repository_context"] = deepcopy(repository_context)

        if detected_type == "user":
            base_context["username"] = self._require_url_info(runtime_context).owner

        if detected_type == "organization":
            base_context["org_name"] = self._require_url_info(runtime_context).owner

        return base_context

    @staticmethod
    def _person_derivations_by_id(
        runtime_context: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        derivations = PipelineOrchestrator._collect_stage_derivations(
            runtime_context,
            STAGE_PERSON_AGENT,
        )
        by_id: dict[str, dict[str, Any]] = {}
        for derivation in derivations:
            person_id = derivation.get("person_id")
            if isinstance(person_id, str) and person_id:
                by_id[person_id] = derivation
        return by_id

    @staticmethod
    def _repository_derivations_by_id(
        runtime_context: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        derivations = PipelineOrchestrator._collect_stage_derivations(
            runtime_context,
            STAGE_REPO_AGENT,
        )
        by_id: dict[str, dict[str, Any]] = {}
        for derivation in derivations:
            repository_id = derivation.get("repository_full_name")
            if isinstance(repository_id, str) and repository_id:
                by_id[repository_id] = derivation
        return by_id

    @staticmethod
    def _merge_person_with_derivation(
        person: dict[str, Any],
        derivation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        merged = deepcopy(person)
        if not isinstance(derivation, dict):
            return merged

        affiliation_names = derivation.get("affiliation_names")
        if isinstance(affiliation_names, list):
            merged["affiliations"] = [
                value
                for value in affiliation_names
                if isinstance(value, str) and value
            ]

        orcid_affiliations = derivation.get("orcid_affiliations")
        if isinstance(orcid_affiliations, list):
            merged["orcid_affiliations"] = [
                deepcopy(value)
                for value in orcid_affiliations
                if isinstance(value, dict)
            ]

        source_repositories = derivation.get("source_repositories")
        if isinstance(source_repositories, list):
            merged["source_repositories"] = [
                value
                for value in source_repositories
                if isinstance(value, str) and value
            ]

        return merged

    @staticmethod
    def _merge_repository_with_derivation(
        repository: dict[str, Any],
        derivation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        merged = deepcopy(repository)
        if not isinstance(derivation, dict):
            return merged

        contributors = derivation.get("contributors")
        if isinstance(contributors, list):
            merged["contributors"] = [
                deepcopy(contributor)
                for contributor in contributors
                if isinstance(contributor, (dict, str))
            ]
        return merged

    def _article_fanout_contexts(  # noqa: C901
        self,
        runtime_context: dict[str, Any],
        detected_type: str,
    ) -> list[dict[str, Any]]:
        base_context = self._class_agent_base_context(runtime_context, detected_type)

        seeds: list[str] = []
        if detected_type == "repository":
            full_name = base_context.get("full_name")
            if isinstance(full_name, str) and full_name:
                seeds.append(full_name)
        if detected_type == "user":
            username = base_context.get("username")
            if isinstance(username, str) and username:
                seeds.append(username)
        if detected_type == "organization":
            org_name = base_context.get("org_name")
            if isinstance(org_name, str) and org_name:
                seeds.append(org_name)

        deduplicated_seeds = _deduplicate(seeds)
        contexts: list[dict[str, Any]] = []
        for seed in deduplicated_seeds:
            context = deepcopy(base_context)
            context["article_seed"] = seed
            if detected_type == "repository":
                context["full_name"] = seed
            if detected_type == "user":
                context["username"] = seed
            if detected_type == "organization":
                context["org_name"] = seed
            contexts.append(context)
        return contexts

    def _membership_fanout_contexts(
        self,
        runtime_context: dict[str, Any],
        detected_type: str,
    ) -> list[dict[str, Any]]:
        base_context = self._class_agent_base_context(runtime_context, detected_type)
        known_persons = base_context.get("known_persons")
        known_organizations = base_context.get("known_organizations")
        if not isinstance(known_persons, list) or not isinstance(known_organizations, list):
            return []
        if not known_persons or not known_organizations:
            return []

        person_derivations = self._person_derivations_by_id(runtime_context)
        contexts: list[dict[str, Any]] = []
        seen_seeds: set[str] = set()

        for person in sorted(
            [item for item in known_persons if isinstance(item, dict)],
            key=lambda item: str(item.get("id", "")),
        ):
            person_id = person.get("id")
            if not isinstance(person_id, str) or not person_id or person_id in seen_seeds:
                continue
            seen_seeds.add(person_id)

            merged_person = self._merge_person_with_derivation(
                person,
                person_derivations.get(person_id),
            )
            context = deepcopy(base_context)
            context["membership_seed"] = person_id
            context["known_persons"] = [merged_person]
            context["known_organizations"] = deepcopy(known_organizations)
            contexts.append(context)

        return contexts

    def _contribution_fanout_contexts(
        self,
        runtime_context: dict[str, Any],
        detected_type: str,
    ) -> list[dict[str, Any]]:
        base_context = self._class_agent_base_context(runtime_context, detected_type)
        known_persons = base_context.get("known_persons")
        known_repositories = base_context.get("known_repositories")
        if not isinstance(known_persons, list) or not isinstance(known_repositories, list):
            return []
        if not known_persons or not known_repositories:
            return []

        repository_derivations = self._repository_derivations_by_id(runtime_context)
        contexts: list[dict[str, Any]] = []
        seen_seeds: set[str] = set()

        for repository in sorted(
            [item for item in known_repositories if isinstance(item, dict)],
            key=lambda item: str(
                item.get("id")
                or item.get("pulse:githubRepositoryHandle")
                or item.get("full_name")
                or "",
            ),
        ):
            repository_id = repository.get("id")
            if not isinstance(repository_id, str) or not repository_id:
                continue
            if repository_id in seen_seeds:
                continue
            seen_seeds.add(repository_id)

            merged_repository = self._merge_repository_with_derivation(
                repository,
                repository_derivations.get(repository_id),
            )
            context = deepcopy(base_context)
            context["contribution_seed"] = repository_id
            context["known_persons"] = deepcopy(known_persons)
            context["known_repositories"] = [merged_repository]
            contexts.append(context)

        return contexts
