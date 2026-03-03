from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

from src.v2.agents.llm import LLMRepositoryAgentV2
from src.v2.agents.runtime import AgentRuntime

if TYPE_CHECKING:
    from src.v2.agents.contracts import RuntimeAgent

AgentRunner = Callable[[dict[str, Any], Any], Any | Awaitable[Any]]

STAGE_REPO_AGENT = "repo_agent"


class AgentRuntimeRegistry:
    """Resolves stage runners based on stage key, runtime, and entity type."""

    def __init__(
        self,
        *,
        rule_based_runners: dict[str, AgentRunner] | None = None,
        llm_repository_agent: RuntimeAgent | None = None,
    ) -> None:
        self._rule_based_runners: dict[str, AgentRunner] = dict(rule_based_runners or {})
        self._llm_repository_agent = llm_repository_agent or LLMRepositoryAgentV2()

    def register_rule_runner(self, stage_key: str, runner: AgentRunner) -> None:
        """Register or replace a rule-based runner for a pipeline stage."""

        self._rule_based_runners[stage_key] = runner

    def resolve_runner(
        self,
        *,
        stage_key: str,
        runtime: AgentRuntime,
        detected_type: str,
    ) -> AgentRunner:
        """Resolve the callable for the requested runtime/stage combination."""

        if (
            runtime == AgentRuntime.LLM
            and detected_type == "repository"
            and stage_key == STAGE_REPO_AGENT
        ):
            return self._llm_repository_agent.run

        runner = self._rule_based_runners.get(stage_key)
        if runner is None:
            message = f"No runner registered for stage '{stage_key}'"
            raise ValueError(message)
        return runner
