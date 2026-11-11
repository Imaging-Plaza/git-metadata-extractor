"""The core extraction entrypoint must work without HTTP request objects."""

# ruff: noqa: PLC0415, PLR2004, SLF001, ARG001, ARG005, PERF401
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest


def test_execution_layers_do_not_import_http_or_api_modules():
    import git_metadata_extractor

    root = Path(git_metadata_extractor.__file__).parent
    paths = [
        *root.joinpath("services").glob("*.py"),
        *root.joinpath("pipeline").rglob("*.py"),
        root / "experiments/execution.py",
        root / "providers/runtime.py",
    ]
    assert root.joinpath("services/extraction.py").is_file()
    violations = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [n.name for n in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for name in modules:
                if (
                    name.split(".")[0] in {"fastapi", "starlette"}
                    or name == "git_metadata_extractor.api"
                    or name.startswith("git_metadata_extractor.api.")
                    or name == "git_metadata_extractor.dependencies"
                ):
                    violations.append(f"{path.name}:{node.lineno}: {name}")
    assert violations == []


def test_plain_service_extracts_and_caches_without_http(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from git_metadata_extractor.observation.query_log import QueryLog, query_log_var
    from git_metadata_extractor.pipeline.runner import Stage
    from git_metadata_extractor.providers.cache import ProviderCache
    from git_metadata_extractor.providers.runtime import build_provider_resources
    from git_metadata_extractor.services import extraction
    from git_metadata_extractor.services.extraction import (
        ExtractionOptions,
        execute_extraction,
    )

    def fail_open_stage(state):
        state.warn("warning from a post-agent stage")
        message = "stage unavailable"
        raise RuntimeError(message)

    monkeypatch.setattr(extraction, "PAYLOAD_CHAIN", [
        *extraction.PAYLOAD_CHAIN,
        Stage(name="warning_probe", run=fail_open_stage),
    ])

    async def run():
        resources = build_provider_resources(
            SimpleNamespace(), use_mock=True, include_rag=False,
        )
        cache = ProviderCache(tmp_path / "providers.db")
        previous = QueryLog("parent", "https://github.com/parent/repo")
        token = query_log_var.set(previous)
        try:
            options = ExtractionOptions(
                source_url="https://github.com/octocat/Hello-World",
                agent_runtime="rule_based",
                read_only=True,
            )
            first = await execute_extraction(
                options, providers=resources.bundle, cache=cache,
            )
            second = await execute_extraction(
                options, providers=resources.bundle, cache=cache,
            )
            assert first.response.output.model_dump(by_alias=True)["@graph"]
            assert not first.cache_hit
            assert second.cache_hit
            assert first.response == second.response
            assert "warning from a post-agent stage" in first.response.warnings
            assert any("warning_probe" in warning for warning in first.response.warnings)
            assert first.run_id != second.run_id
            assert query_log_var.get() is previous
            monkeypatch.setattr(extraction, "mapping_manifest", lambda: {"fingerprint": "changed"})
            third = await execute_extraction(options, providers=resources.bundle, cache=cache)
            assert not third.cache_hit
        finally:
            query_log_var.reset(token)
            await resources.aclose()

    asyncio.run(run())


def test_plain_service_returns_typed_domain_error_for_unsupported_url():
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.services.extraction import (
        ExtractionError,
        ExtractionOptions,
        execute_extraction,
    )

    with pytest.raises(ExtractionError) as caught:
        asyncio.run(
            execute_extraction(
                ExtractionOptions(source_url="https://github.com/org/repo/issues/1"),
                providers=ProviderSet(github=None),
            ),
        )
    assert caught.value.error.error_type.value == "unsupported_url"


def test_provider_resources_close_owned_unique_resources_despite_failures():
    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.providers.runtime import ProviderResources

    closed = []

    class AsyncProvider:
        async def aclose(self):
            closed.append("async")

    class BrokenProvider:
        def close(self):
            closed.append("broken")
            raise RuntimeError

    class BorrowedProvider:
        def close(self):
            pytest.fail("Borrowed provider was closed")

    owned = AsyncProvider()
    resources = ProviderResources(
        bundle=ProviderSet(github=BorrowedProvider()), owned=[BrokenProvider(), owned, owned],
    )
    asyncio.run(resources.aclose())
    asyncio.run(resources.aclose())
    assert closed == ["broken", "async"]


def test_injected_bundle_is_borrowed_and_no_providers_are_built():
    from types import SimpleNamespace

    from git_metadata_extractor.agents.models import ProviderSet
    from git_metadata_extractor.providers.runtime import build_provider_resources

    bundle = ProviderSet(github=object())
    resources = build_provider_resources(
        SimpleNamespace(v2_provider_set=bundle), use_mock=False,
    )
    assert resources.bundle is bundle
    assert resources.owned == []


def test_cleanup_survives_cancellation_and_can_be_awaited_again():
    from git_metadata_extractor.providers.runtime import ProviderResources

    closed = []
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        class Slow:
            async def aclose(self):
                started.set()
                await release.wait()
                closed.append("slow")
        resources = ProviderResources(owned=[Slow()])
        cleanup = asyncio.create_task(resources.aclose())
        await started.wait()
        cleanup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cleanup
        release.set()
        await resources.aclose()
        assert closed == ["slow"]
    asyncio.run(run())


def test_failed_primary_construction_closes_previously_created_clients(monkeypatch):
    from types import SimpleNamespace

    from git_metadata_extractor.providers import runtime

    closed = []

    class Github:
        def close(self):
            closed.append("github")

    def broken():
        raise RuntimeError

    monkeypatch.setattr(runtime, "MockGitHubProvider", Github)
    monkeypatch.setattr(runtime, "MockORCIDProvider", broken)
    with pytest.raises(RuntimeError):
        runtime.build_provider_resources(
            SimpleNamespace(), use_mock=True, include_rag=False,
        )
    assert closed == ["github"]


def test_shared_rag_retries_unavailable_then_closes_only_at_shutdown(monkeypatch):
    from types import SimpleNamespace

    from git_metadata_extractor.providers import runtime

    closed, attempts = [], []

    class Rag:
        async def aclose(self):
            closed.append("rag")

    shared = Rag()

    def build():
        attempts.append(1)
        return None if len(attempts) == 1 else shared

    spec = runtime.ProviderRegistration(
        name="infoscience_rag", env_var="TEST_RAG_ENABLED", builder=build, expected_type=Rag,
    )
    monkeypatch.setattr(runtime, "RAG_PROVIDERS", (spec,))
    state = SimpleNamespace()
    first = runtime.build_provider_resources(state, use_mock=True)
    second = runtime.build_provider_resources(state, use_mock=True)
    third = runtime.build_provider_resources(state, use_mock=True)
    assert first.bundle.infoscience_rag is None
    assert second.bundle.infoscience_rag is third.bundle.infoscience_rag is shared
    for resources in (first, second, third):
        asyncio.run(resources.aclose())
    assert not closed
    asyncio.run(runtime.close_shared_providers(state))
    assert closed == ["rag"]
    assert len(attempts) == 2


def test_http_dependency_closes_owned_clients_on_error(monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from git_metadata_extractor import dependencies
    from git_metadata_extractor.api import v2_router
    from git_metadata_extractor.providers.runtime import (
        ProviderResources,
        build_provider_resources,
    )

    closed = []

    class Owned:
        async def aclose(self):
            closed.append("owned")

    bundle = build_provider_resources(
        SimpleNamespace(), use_mock=True, include_rag=False,
    ).bundle
    monkeypatch.setattr(
        dependencies,
        "build_provider_resources",
        lambda state, **kwargs: ProviderResources(bundle=bundle, owned=[Owned()]),
    )

    async def run():
        app = FastAPI()
        app.include_router(v2_router)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-api-token"},
        ) as client:
            response = await client.get("/v2/extract/github.com/org/repo/issues/1")
            assert response.status_code == 422

    asyncio.run(run())
    assert closed == ["owned"]


def test_shutdown_drains_jobs_before_shared_providers():
    from types import SimpleNamespace

    from git_metadata_extractor.app import shutdown_event
    from git_metadata_extractor.providers.runtime import ProviderResources

    events = []

    class Shared:
        async def aclose(self):
            events.append("shared closed")

    async def run():
        started = asyncio.Event()

        async def job():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append("job stopped")

        task = asyncio.create_task(job())
        await started.wait()
        app = SimpleNamespace(
            state=SimpleNamespace(
                _v2_job_tasks={task},
                v2_shared_provider_resources=ProviderResources(owned=[Shared()]),
            ),
        )
        try:
            await shutdown_event(app)
            assert task.done()
            assert events == ["job stopped", "shared closed"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_job_cancelled_before_start_still_releases_deferred_resources():
    from types import SimpleNamespace

    from git_metadata_extractor.dependencies import defer_provider_resources
    from git_metadata_extractor.providers.runtime import ProviderResources

    closed = []

    class Owned:
        async def aclose(self):
            closed.append("closed")

    async def run():
        async def never_started():
            pytest.fail("Task should have been cancelled before entering")

        request = SimpleNamespace(
            state=SimpleNamespace(
                v2_provider_resources=ProviderResources(owned=[Owned()]),
            ),
            app=SimpleNamespace(state=SimpleNamespace()),
        )
        task = asyncio.create_task(never_started())
        defer_provider_resources(request, task)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*request.app.state._v2_provider_cleanup_tasks)

    asyncio.run(run())
    assert closed == ["closed"]


def test_cancelled_job_closes_resources_after_heartbeat_cancellation(
    tmp_path, monkeypatch,
):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from git_metadata_extractor.api import extract as module
    from git_metadata_extractor.api_models import (
        V2ExtractJob,
        V2ExtractJobStatus,
        V2ExtractRequest,
    )
    from git_metadata_extractor.jobs import JobStore
    from git_metadata_extractor.providers.cache import ProviderCache
    from git_metadata_extractor.providers.runtime import ProviderResources

    closed = []

    class Owned:
        async def aclose(self):
            closed.append("owned")

    async def run():
        started = asyncio.Event()

        async def blocked(**kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(module, "_extract_for_request", blocked)
        payload = V2ExtractRequest(
            source_url="https://github.com/octocat/Hello-World",
            agent_runtime="rule_based",
        )
        store = JobStore(ProviderCache(tmp_path / "job.db"))
        store.set(
            V2ExtractJob(
                job_id="cancel",
                status=V2ExtractJobStatus.PENDING,
                request=payload,
                submitted_at=datetime.now(timezone.utc),
            ),
        )
        request = SimpleNamespace(
            state=SimpleNamespace(
                v2_provider_resources=ProviderResources(owned=[Owned()]),
            ),
        )
        task = asyncio.create_task(
            module._run_extract_job(
                payload=payload,
                request=request,
                providers=None,
                job_store=store,
                job_id="cancel",
            ),
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store.get("cancel").status == V2ExtractJobStatus.CANCELLED

    asyncio.run(run())
    assert closed == ["owned"]
