from __future__ import annotations

from typing import TYPE_CHECKING

from git_metadata_extractor.agents.llm import (
    LLMArticleAgentV2,
    LLMContributionAgentV2,
    LLMLinkVeracityAgentV2,
    LLMMembershipAgentV2,
    LLMOrganizationAgentV2,
    LLMPersonAgentV2,
    LLMRepositoryAgentV2,
)
from git_metadata_extractor.agents.rule_based import (
    ArticleAgentV2,
    ContributionAgentV2,
    MembershipAgentV2,
    OrganizationAgentV2,
    PersonAgentV2,
    RepositoryAgentV2,
)

if TYPE_CHECKING:
    from git_metadata_extractor.agents.contracts import RuntimeAgent


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


def test_llm_namespace_exposes_person_runtime_agent() -> None:
    agent: RuntimeAgent = LLMPersonAgentV2()
    _assert_runtime_agent_shape(agent)
    assert isinstance(agent, LLMPersonAgentV2)


def test_llm_namespace_exposes_organization_runtime_agent() -> None:
    agent: RuntimeAgent = LLMOrganizationAgentV2()
    _assert_runtime_agent_shape(agent)
    assert isinstance(agent, LLMOrganizationAgentV2)


def test_llm_namespace_exposes_article_runtime_agent() -> None:
    agent: RuntimeAgent = LLMArticleAgentV2()
    _assert_runtime_agent_shape(agent)
    assert isinstance(agent, LLMArticleAgentV2)


def test_llm_namespace_exposes_membership_runtime_agent() -> None:
    agent: RuntimeAgent = LLMMembershipAgentV2()
    _assert_runtime_agent_shape(agent)
    assert isinstance(agent, LLMMembershipAgentV2)


def test_llm_namespace_exposes_contribution_runtime_agent() -> None:
    agent: RuntimeAgent = LLMContributionAgentV2()
    _assert_runtime_agent_shape(agent)
    assert isinstance(agent, LLMContributionAgentV2)


def test_llm_namespace_exposes_link_veracity_runtime_agent() -> None:
    agent: RuntimeAgent = LLMLinkVeracityAgentV2()
    _assert_runtime_agent_shape(agent)
    assert isinstance(agent, LLMLinkVeracityAgentV2)
