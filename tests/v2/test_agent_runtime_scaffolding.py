from __future__ import annotations

from typing import TYPE_CHECKING

from src.v2.agents.llm import LLMRepositoryAgentV2
from src.v2.agents.rule_based import (
    ArticleAgentV2,
    ContributionAgentV2,
    MembershipAgentV2,
    OrganizationAgentV2,
    PersonAgentV2,
    RepositoryAgentV2,
)

if TYPE_CHECKING:
    from src.v2.agents.contracts import RuntimeAgent


def _assert_runtime_agent_shape(agent: RuntimeAgent) -> None:
    run_attr = getattr(agent, "run", None)
    assert callable(run_attr)


def test_rule_based_namespace_re_exports_current_agents() -> None:
    _assert_runtime_agent_shape(RepositoryAgentV2())
    _assert_runtime_agent_shape(PersonAgentV2())
    _assert_runtime_agent_shape(OrganizationAgentV2())
    _assert_runtime_agent_shape(ArticleAgentV2())
    _assert_runtime_agent_shape(MembershipAgentV2())
    _assert_runtime_agent_shape(ContributionAgentV2())


def test_llm_namespace_exposes_repository_runtime_agent() -> None:
    agent: RuntimeAgent = LLMRepositoryAgentV2()
    _assert_runtime_agent_shape(agent)
    assert isinstance(agent, LLMRepositoryAgentV2)
