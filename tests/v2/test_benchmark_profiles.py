# Test-local imports and literal expected metrics make each case self-contained.
# ruff: noqa: PLC0415, PLR2004
from __future__ import annotations

import asyncio
import json

import pytest


def test_metrics_do_not_count_historical_or_pipeline_cached_tokens():
    from git_metadata_extractor.experiments.benchmark import BenchmarkCase, measure

    response = {
        "stats": {"run_id": "new", "entities_count": 1, "triples_count": 1},
        "warnings": [],
        "output": {
            "@context": {},
            "@graph": [{"@id": "https://example.org/repo", "schema:name": "Tool"}],
        },
        "provenance": {
            "@graph": [
                {
                    "@id": "urn:pulse:run:old:activity:1",
                    "gme:kind": "model",
                    "gme:status": "success",
                    "gme:tokens_prompt": 999,
                    "gme:tokens_completion": 888,
                },
                {
                    "@id": "urn:pulse:run:new:activity:2",
                    "gme:kind": "model",
                    "gme:status": "success",
                    "gme:tokens_prompt": 10,
                    "gme:tokens_completion": 4,
                },
            ],
        },
    }
    case = BenchmarkCase(
        id="case",
        source_url="https://github.com/acme/tool",
        expected_facts=[
            {
                "subject": "https://example.org/repo",
                "predicate": "schema:name",
                "value": "Tool",
            },
        ],
    )
    metrics = measure(response, case, pipeline_cache_hit=False, validate=False)
    assert metrics["tokens_prompt"] == 10
    assert metrics["tokens_completion"] == 4
    assert metrics["expected_fact_recall"] == 1.0
    assert metrics["shacl_conforms"] is None
    cached = measure(response, case, pipeline_cache_hit=True, validate=False)
    assert cached["tokens_prompt"] == 0
    assert cached["model_calls"] == 0


def test_failed_trial_is_retained_and_does_not_stop_matrix(tmp_path):
    from git_metadata_extractor.experiments.benchmark import (
        BenchmarkCase,
        run_benchmark,
    )
    from git_metadata_extractor.experiments.models import PipelineProfile

    cases = [
        BenchmarkCase(id="missing", source_url="https://github.com/missing/repo"),
        BenchmarkCase(id="known", source_url="https://github.com/octocat/Hello-World"),
    ]
    output = tmp_path / "benchmark"
    report = asyncio.run(
        run_benchmark(
            [PipelineProfile(name="baseline", retry_max_retries=0)],
            cases,
            output_dir=output,
            provider_mode="mock",
        ),
    )
    assert [row["status"] for row in report["trials"]] == ["failed", "completed"]
    assert (output / "baseline" / "missing" / "1" / "trial.json").exists()
    assert (output / "baseline" / "known" / "1" / "result.json").exists()
    assert (
        json.loads((output / "summary.json").read_text())["profiles"]["baseline"][
            "failed"
        ]
        == 1
    )


def test_warm_pipeline_trial_is_primed_and_labelled(tmp_path, monkeypatch):
    from git_metadata_extractor.experiments.benchmark import (
        BenchmarkCase,
        run_benchmark,
    )
    from git_metadata_extractor.experiments.models import PipelineProfile

    monkeypatch.setenv("V2_PIPELINE_CACHE_ENABLED", "true")
    report = asyncio.run(
        run_benchmark(
            [PipelineProfile(name="baseline")],
            [
                BenchmarkCase(
                    id="known", source_url="https://github.com/octocat/Hello-World",
                ),
            ],
            output_dir=tmp_path / "warm",
            cache_mode="warm_pipeline",
        ),
    )
    trial = report["trials"][0]
    assert trial["pipeline_cache_hit"] is True
    assert trial["cache_mode"] == "warm_pipeline"
    assert trial["metrics"]["tokens_prompt"] == 0


def test_duplicate_profile_names_are_rejected_before_writing(tmp_path):
    from git_metadata_extractor.experiments.benchmark import (
        BenchmarkCase,
        run_benchmark,
    )
    from git_metadata_extractor.experiments.models import PipelineProfile

    with pytest.raises(ValueError, match="unique"):
        asyncio.run(
            run_benchmark(
                [PipelineProfile(name="same"), PipelineProfile(name="same")],
                [
                    BenchmarkCase(
                        id="known", source_url="https://github.com/octocat/Hello-World",
                    ),
                ],
                output_dir=tmp_path / "new",
            ),
        )
    assert not (tmp_path / "new").exists()


def test_benchmark_does_not_validate_legacy_output_against_v3_shapes(
    tmp_path, monkeypatch,
):
    from git_metadata_extractor.experiments.benchmark import (
        BenchmarkCase,
        run_benchmark,
    )
    from git_metadata_extractor.experiments.models import PipelineProfile

    monkeypatch.setenv("V2_CANONICAL_OUTPUT_ENABLED", "false")
    with pytest.raises(ValueError, match="canonical"):
        asyncio.run(
            run_benchmark(
                [PipelineProfile(name="legacy")],
                [
                    BenchmarkCase(
                        id="repo", source_url="https://github.com/octocat/Hello-World",
                    ),
                ],
                output_dir=tmp_path / "out",
            ),
        )
    assert not (tmp_path / "out").exists()
