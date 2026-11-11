"""Optional run reports describe this request, including cached and failed runs."""

# ruff: noqa: PLC0415, ARG001
import asyncio
from types import SimpleNamespace

import pytest

from git_metadata_extractor.api_models import V2ExtractRequest
from git_metadata_extractor.pipeline import PipelineOrchestrator
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.providers.runtime import build_provider_resources
from git_metadata_extractor.services.extraction import (
    ExtractionError,
    ExtractionOptions,
    execute_extraction,
)


def test_report_is_opt_in_in_request_and_service_contracts():
    source = "https://github.com/octocat/Hello-World"
    assert V2ExtractRequest(source_url=source).include_run_report is False
    assert ExtractionOptions(source_url=source).include_run_report is False
    assert V2ExtractRequest(source_url=source, include_run_report=True).include_run_report is True


@pytest.mark.parametrize("first_requested", [False, True])
def test_report_selection_does_not_rerun_cached_extractions(tmp_path, first_requested):
    async def run():
        resources = build_provider_resources(SimpleNamespace(), use_mock=True, include_rag=False)
        cache = ProviderCache(tmp_path / "providers.db")
        options = ExtractionOptions(
            source_url="https://github.com/octocat/Hello-World",
            agent_runtime="rule_based",
            read_only=True,
            include_run_report=first_requested,
        )
        try:
            first = await execute_extraction(options, providers=resources.bundle, cache=cache)
            assert not first.cache_hit
            assert (first.response.run_report is not None) is first_requested
            if first_requested:
                report = first.response.run_report
                assert report.request_run_id == first.run_id
                assert report.output_run_id == first.response.stats.run_id
                assert report.runtime == "rule_based"
                assert report.status == "completed"
                assert any(stage.phase == "generation" for stage in report.stages)
                assert any(stage.phase == "post_processing" for stage in report.stages)
                assert any(stage.name == "refine_with_llm" and stage.status == "skipped"
                           for stage in report.stages)
                assert report.validations

            requested = options.model_copy(update={"include_run_report": True})
            hit = await execute_extraction(requested, providers=resources.bundle, cache=cache)
            assert hit.cache_hit
            report = hit.response.run_report
            assert report.status == "cache_hit"
            assert report.request_run_id == hit.run_id != first.run_id
            assert report.output_run_id == first.response.stats.run_id
            assert report.stages == []
            assert report.activities == []
            assert report.retrievals == []
            assert report.usage.model_calls == 0
            assert hit.response.output == first.response.output

            disabled = options.model_copy(update={"include_run_report": False})
            last = await execute_extraction(disabled, providers=resources.bundle, cache=cache)
            assert last.cache_hit
            assert last.response.run_report is None
            assert "run_report" not in last.response.model_dump(exclude_none=True)
        finally:
            await resources.aclose()

    asyncio.run(run())


def test_domain_failure_carries_partial_report_without_exception_text():
    async def broken_context(*args):
        message = "private exception payload"
        raise RuntimeError(message)

    async def run():
        resources = build_provider_resources(SimpleNamespace(), use_mock=True, include_rag=False)
        try:
            with pytest.raises(ExtractionError) as caught:
                await execute_extraction(
                    ExtractionOptions(
                        source_url="https://github.com/octocat/Hello-World",
                        agent_runtime="rule_based",
                        include_run_report=True,
                        read_only=True,
                    ),
                    providers=resources.bundle,
                    orchestrator=PipelineOrchestrator(context_gatherer=broken_context),
                )
            report = caught.value.error.run_report
            assert report.status == "failed"
            assert any(stage.name == "context_gather" and stage.status == "failed"
                       for stage in report.stages)
            assert "private exception payload" not in report.model_dump_json()
        finally:
            await resources.aclose()

    asyncio.run(run())


def test_local_profile_can_request_a_report_without_changing_its_fingerprint(tmp_path):
    from git_metadata_extractor.experiments.execution import run_profile
    from git_metadata_extractor.experiments.models import PipelineProfile

    async def run():
        profile = PipelineProfile(name="observed", runtime="rule_based")
        kwargs = {"cache_dir": tmp_path, "provider_mode": "mock"}
        first = await run_profile(profile, "https://github.com/octocat/Hello-World", **kwargs)
        second = await run_profile(
            profile, "https://github.com/octocat/Hello-World", include_run_report=True, **kwargs,
        )
        assert first.response.run_report is None
        assert second.pipeline_cache_hit
        assert second.response.run_report.status == "cache_hit"
        assert second.manifest["fingerprint"] == first.manifest["fingerprint"]

    asyncio.run(run())
