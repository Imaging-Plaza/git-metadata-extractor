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


def test_person_agent_fanout_stage_is_parallelizable() -> None:
    orchestrator = PipelineOrchestrator()
    plan = orchestrator.get_execution_plan("repository")

    person_stage = next(stage for stage in plan.stages if stage.name == "person_agents")
    article_stage = next(stage for stage in plan.stages if stage.name == "article_agents")
    membership_stage = next(
        stage for stage in plan.stages if stage.name == "membership_agents"
    )
    contribution_stage = next(
        stage for stage in plan.stages if stage.name == "contribution_agents"
    )

    assert person_stage.groups[0].parallelizable is True
    assert article_stage.groups[0].parallelizable is True
    assert membership_stage.groups[0].parallelizable is True
    assert contribution_stage.groups[0].parallelizable is True


def test_execution_plans_have_no_circular_dependencies() -> None:
    orchestrator = PipelineOrchestrator()

    for detected_type in ("repository", "user", "organization"):
        plan = orchestrator.get_execution_plan(detected_type)
        assert plan.has_circular_dependencies() is False


def test_execution_plan_is_serializable_to_dict() -> None:
    orchestrator = PipelineOrchestrator()
    plan = orchestrator.get_execution_plan("repository")

    serialized = plan.to_dict()

    assert serialized["detected_type"] == "repository"
    assert serialized["stages"][0]["name"] == "context_gather"
    assert isinstance(serialized["stages"][0]["groups"], list)
