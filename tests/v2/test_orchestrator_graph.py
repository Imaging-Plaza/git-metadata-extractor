from __future__ import annotations

from git_metadata_extractor.pipeline import PipelineOrchestrator


def test_repository_execution_plan_stage_order() -> None:
    orchestrator = PipelineOrchestrator()

    plan = orchestrator.get_execution_plan("repository")

    assert [stage.name for stage in plan.stages] == [
        "context_gather",
        "repo_agent",
        "person_agents",
        "org_agents",
        "article_agents",
        "membership_agents",
        "contribution_agents",
    ]


def test_user_execution_plan_stage_order() -> None:
    orchestrator = PipelineOrchestrator()

    plan = orchestrator.get_execution_plan("user")

    assert [stage.name for stage in plan.stages] == [
        "context_gather",
        "person_agent",
        "repo_agents",
        "org_agents",
        "article_agents",
        "membership_agents",
        "contribution_agents",
    ]


def test_organization_execution_plan_stage_order() -> None:
    orchestrator = PipelineOrchestrator()

    plan = orchestrator.get_execution_plan("organization")

    assert [stage.name for stage in plan.stages] == [
        "context_gather",
        "org_agent",
        "person_agents",
        "repo_agents",
        "article_agents",
        "membership_agents",
        "contribution_agents",
    ]
