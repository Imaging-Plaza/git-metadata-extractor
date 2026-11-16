"""Account-type filtering must not block orchestration or request deadlines."""
from __future__ import annotations

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest

from git_metadata_extractor.agents import ProviderSet
from git_metadata_extractor.agents.models import AgentResult
from git_metadata_extractor.pipeline import PipelineOrchestrator
from git_metadata_extractor.pipeline.stages.models import ContextBundle
from git_metadata_extractor.providers.detection import classify_github_url


async def _execute(stage_name, get_user):
    async def context_gatherer(*_args):
        return ContextBundle(detected_type="repository", context={
            "repository": {
                "full_name": "lab/project",
                "metadata": {"owner": {"login": "lab", "type": "Organization"}},
                "contributors": [{"login": "alice"}, {"login": "other-lab"}],
            },
        })

    async def agent(context, _providers):
        return AgentResult(data={"id": context.get("username") or context["org_name"]})

    orchestrator = PipelineOrchestrator(
        context_gatherer=context_gatherer,
        agent_runners={"person_agent": agent, "org_agent": agent},
    )
    plan = orchestrator.get_execution_plan("repository")
    plan = plan.model_copy(update={
        "stages": [stage for stage in plan.stages if stage.name in {"context_gather", stage_name}],
    })
    return await orchestrator.execute(
        plan=plan, providers=ProviderSet(github=SimpleNamespace(get_user=get_user)),
        context={
            "url_info": classify_github_url("https://github.com/lab/project"),
            "source_url": "https://github.com/lab/project",
        },
    )


@pytest.mark.parametrize("stage_name", ["person_agents", "org_agents"])
def test_prefilter_yields_event_loop_and_preserves_filter_results(stage_name):
    async def exercise():
        loop = asyncio.get_running_loop()
        calls = []
        responsive = []

        def get_user(username):
            release = Event()
            loop.call_soon_threadsafe(release.set)
            responsive.append(release.wait(1))
            calls.append(username)
            return {"login": username, "type": "User" if username == "alice" else "Organization"}

        result = await _execute(stage_name, get_user)
        assert responsive
        assert all(responsive)
        if stage_name == "person_agents":
            assert calls == ["alice", "other-lab"]
            assert set(result.agent_results) == {"person_agent:alice"}
            assert any("other-lab" in warning for warning in result.warnings)
        else:
            assert calls == ["lab"]
            assert set(result.agent_results) == {"org_agent:lab"}

    asyncio.run(exercise())


@pytest.mark.parametrize("stage_name", ["person_agents", "org_agents"])
def test_prefilter_allows_request_deadline_to_cancel(stage_name):
    release = Event()
    completed = Event()
    calls = []

    def get_user(username):
        calls.append(username)
        release.wait(1)
        completed.set()
        return {"login": username, "type": "Organization"}

    async def exercise():
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(_execute(stage_name, get_user), timeout=0.05)
            assert not completed.is_set()
        finally:
            release.set()

    asyncio.run(exercise())
    assert calls == (["alice"] if stage_name == "person_agents" else ["lab"])
