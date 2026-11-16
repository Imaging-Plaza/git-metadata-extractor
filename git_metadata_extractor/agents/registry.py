from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

from git_metadata_extractor.agents.llm import (
    LLMArticleAgentV2,
    LLMContributionAgentV2,
    LLMMembershipAgentV2,
    LLMOrganizationAgentV2,
    LLMPersonAgentV2,
    LLMRepositoryAgentV2,
)
from git_metadata_extractor.agents.runtime import AgentRuntime

if TYPE_CHECKING:
    from git_metadata_extractor.agents.contracts import RuntimeAgent

AgentRunner = Callable[[dict[str, Any], Any], Any | Awaitable[Any]]

STAGE_REPO_AGENT = "repo_agent"
STAGE_PERSON_AGENT = "person_agent"
STAGE_ORG_AGENT = "org_agent"
STAGE_ARTICLE_AGENT = "article_agent"
STAGE_MEMBERSHIP_AGENT = "membership_agent"
STAGE_CONTRIBUTION_AGENT = "contribution_agent"


class AgentRuntimeRegistry:
    """Resolves stage runners based on stage key, runtime, and entity type."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        rule_based_runners: dict[str, AgentRunner] | None = None,
        llm_repository_agent: RuntimeAgent | None = None,
        llm_person_agent: RuntimeAgent | None = None,
        llm_organization_agent: RuntimeAgent | None = None,
        llm_article_agent: RuntimeAgent | None = None,
        llm_membership_agent: RuntimeAgent | None = None,
        llm_contribution_agent: RuntimeAgent | None = None,
    ) -> None:
        self._rule_based_runners: dict[str, AgentRunner] = dict(rule_based_runners or {})
        self._llm_repository_agent = llm_repository_agent or LLMRepositoryAgentV2()
        self._llm_person_agent = llm_person_agent or LLMPersonAgentV2()
        self._llm_organization_agent = llm_organization_agent or LLMOrganizationAgentV2()
        self._llm_article_agent = llm_article_agent or LLMArticleAgentV2()
        self._llm_membership_agent = llm_membership_agent or LLMMembershipAgentV2()
        self._llm_contribution_agent = llm_contribution_agent or LLMContributionAgentV2()
        self._runners: dict[tuple[AgentRuntime, str], AgentRunner] = {}
        for stage, agent in {
            STAGE_REPO_AGENT: self._llm_repository_agent,
            STAGE_PERSON_AGENT: self._llm_person_agent,
            STAGE_ORG_AGENT: self._llm_organization_agent,
            STAGE_ARTICLE_AGENT: self._llm_article_agent,
            STAGE_MEMBERSHIP_AGENT: self._llm_membership_agent,
            STAGE_CONTRIBUTION_AGENT: self._llm_contribution_agent,
        }.items():
            self.register_runner(stage, agent.run, runtime=AgentRuntime.LLM)

    def register_runner(
        self, stage_key: str, runner: AgentRunner, *, runtime: AgentRuntime,
    ) -> None:
        """Register an implementation without extending the dispatch function."""
        self._runners[(AgentRuntime(runtime), stage_key)] = runner

    def register_rule_runner(self, stage_key: str, runner: AgentRunner) -> None:
        """Register or replace a rule-based runner for a pipeline stage."""

        self._rule_based_runners[stage_key] = runner

    def resolve_runner(  # noqa: PLR0911
        self,
        *,
        stage_key: str,
        runtime: AgentRuntime,
        detected_type: str,
    ) -> AgentRunner:
        """Resolve the callable for the requested runtime/stage combination."""

        del detected_type

        registered = self._runners.get((runtime, stage_key))
        if registered is not None:
            return registered

        runner = self._rule_based_runners.get(stage_key)
        if runner is None:
            message = f"No runner registered for stage '{stage_key}'"
            raise ValueError(message)
        return runner
