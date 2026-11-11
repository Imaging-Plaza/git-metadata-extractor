"""Retain every trial; distinguish output quality from execution/cache costs."""

from __future__ import annotations

import asyncio
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from git_metadata_extractor.experiments.builtins import default_catalog
from git_metadata_extractor.experiments.execution import execution_manifest, run_profile
from git_metadata_extractor.providers.detection.github_url_classifier import (
    classify_github_url,
)
from git_metadata_extractor.validation.layers import Layer, validate_layer

if TYPE_CHECKING:
    from git_metadata_extractor.experiments.models import PipelineProfile


class ExpectedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str
    predicate: str
    value: JsonValue


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    source_url: str
    expected_facts: list[ExpectedFact] = Field(default_factory=list)

    @field_validator("source_url")
    @classmethod
    def supported_url(cls, value):
        return classify_github_url(value, probe_accounts=False).normalized_url


def _value(value):
    return (
        value.get("@id", value.get("id", value)) if isinstance(value, dict) else value
    )


def measure(
    response: dict,
    case: BenchmarkCase,
    *,
    pipeline_cache_hit: bool,
    validate: bool = True,
) -> dict:
    graph = response["output"]["@graph"]
    nodes = {node["@id"]: node for node in graph}
    matched = 0
    for fact in case.expected_facts:
        actual = nodes.get(fact.subject, {}).get(fact.predicate)
        values = actual if isinstance(actual, list) else [actual]
        matched += any(_value(value) == _value(fact.value) for value in values)
    evidence = (response.get("provenance") or {}).get("@graph", [])
    prefix = f"urn:pulse:run:{response['stats']['run_id']}:"
    current = (
        []
        if pipeline_cache_hit
        else [n for n in evidence if n["@id"].startswith(prefix)]
    )
    models = [n for n in current if n.get("gme:kind") == "model"]
    assertions = [n for n in evidence if n.get("@type") == "rdf:Statement"]
    metrics = {
        "entities": len(graph),
        "triples": response["stats"]["triples_count"],
        "warnings": len(response.get("warnings", [])),
        "model_calls": len(models),
        "failed_model_calls": sum(n.get("gme:status") != "success" for n in models),
        "tokens_prompt": sum(n.get("gme:tokens_prompt", 0) for n in models),
        "tokens_completion": sum(n.get("gme:tokens_completion", 0) for n in models),
        "model_requests": sum(n.get("gme:requests", 0) for n in models),
        "tool_calls": sum(n.get("gme:tool_calls", 0) for n in models),
        "usage_complete": all(
            "gme:tokens_prompt" in n
            and "gme:tokens_completion" in n
            and n.get("gme:status") == "success"
            for n in models
        ),
        "retrieval_events": sum("gme:service" in n for n in current),
        "failed_retrievals": sum(
            "gme:service" in n and n.get("gme:status") == "failed" for n in current
        ),
        "assertions": len(assertions),
        "cited_assertions": sum("prov:wasDerivedFrom" in n for n in assertions),
        "candidate_assertions": sum("gme:matchingQuery" in n for n in assertions),
        "expected_facts": len(case.expected_facts),
        "matched_facts": matched,
        "expected_fact_recall": matched / len(case.expected_facts)
        if case.expected_facts
        else None,
        "shacl_conforms": None,
        "shacl_violations": None,
        "shacl_checked": False,
    }
    if validate:
        validation = validate_layer(response["output"], Layer.CANONICAL)
        metrics.update(
            shacl_checked=not validation.skipped,
            shacl_reason=validation.reason,
            shacl_conforms=None if validation.skipped else validation.conforms,
            shacl_violations=None if validation.skipped else len(validation.violations),
        )
    return metrics


def _write(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )


def _summary(trials: list[dict]) -> dict:
    profiles = {}
    for name in dict.fromkeys(row["profile"] for row in trials):
        rows = [r for r in trials if r["profile"] == name]
        completed = [r for r in rows if r["status"] == "completed"]
        profiles[name] = {
            "completed": len(completed),
            "failed": len(rows) - len(completed),
            "median_seconds": median(r["elapsed_seconds"] for r in completed)
            if completed
            else None,
            "tokens_prompt": sum(r["metrics"]["tokens_prompt"] for r in completed),
            "tokens_completion": sum(
                r["metrics"]["tokens_completion"] for r in completed
            ),
            "pipeline_cache_hits": sum(r["pipeline_cache_hit"] for r in completed),
        }
    return {"profiles": profiles, "trials": trials}


def _write_trials_csv(path: Path, trials: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        flattened = [
            {
                **{k: v for k, v in row.items() if k != "metrics"},
                **row.get("metrics", {}),
            }
            for row in trials
        ]
        columns = list(dict.fromkeys(key for row in flattened for key in row))
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(flattened)


async def run_benchmark(  # noqa: C901, PLR0913 - explicit matrix controls and trial lifecycle
    profiles: list[PipelineProfile],
    cases: list[BenchmarkCase],
    *,
    output_dir: Path,
    catalog=None,
    repeats: int = 1,
    provider_mode: str = "mock",
    cache_mode: str = "cold",
    timeout_seconds: float = 1800,
) -> dict:
    if not profiles or not cases or repeats < 1 or timeout_seconds <= 0:
        msg = "Profiles, cases, positive repeats and timeout are required"
        raise ValueError(msg)
    if len({p.name for p in profiles}) != len(profiles) or len(
        {c.id for c in cases},
    ) != len(cases):
        msg = "Profile names and case IDs must be unique"
        raise ValueError(msg)
    if cache_mode not in {"cold", "warm_pipeline"}:
        msg = "cache_mode must be cold or warm_pipeline"
        raise ValueError(msg)
    if provider_mode not in {"mock", "live"}:
        msg = "provider_mode must be mock or live"
        raise ValueError(msg)
    if os.getenv("V2_CANONICAL_OUTPUT_ENABLED", "true").strip().lower() in {
        "false",
        "0",
        "no",
        "off",
        "f",
        "n",
    }:
        msg = "Benchmark SHACL metrics require canonical output (V2_CANONICAL_OUTPUT_ENABLED=true)"
        raise ValueError(msg)
    catalog = catalog or default_catalog()
    for profile in profiles:
        catalog.validate(profile)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    _write(
        output_dir / "manifest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cache_mode": cache_mode,
            "provider_mode": provider_mode,
            "repeats": repeats,
            "timeout_seconds": timeout_seconds,
            "profiles": [
                execution_manifest(catalog, p, provider_mode) for p in profiles
            ],
            "cases": [c.model_dump(mode="json") for c in cases],
            "execution": "sequential trials; profile order rotates by repetition",
        },
    )
    trials = []
    for repetition in range(repeats):
        offset = repetition % len(profiles)
        ordered = profiles[offset:] + profiles[:offset]
        for case in cases:
            for profile in ordered:
                directory = output_dir / profile.name / case.id / str(repetition + 1)
                directory.mkdir(parents=True)
                row = {
                    "profile": profile.name,
                    "case": case.id,
                    "repetition": repetition + 1,
                    "source_url": case.source_url,
                    "cache_mode": cache_mode,
                    "provider_mode": provider_mode,
                }
                started = perf_counter()
                phase = "priming" if cache_mode == "warm_pipeline" else "execution"
                try:

                    async def execute(profile=profile, case=case, directory=directory):
                        return await run_profile(
                            profile,
                            case.source_url,
                            cache_dir=directory / "cache",
                            catalog=catalog,
                            provider_mode=provider_mode,
                        )

                    if cache_mode == "warm_pipeline":
                        primer = await asyncio.wait_for(
                            execute(), timeout=timeout_seconds,
                        )
                        _write(directory / "priming-result.json", primer.response.model_dump(mode="json", by_alias=True))
                    phase = "execution"
                    started = perf_counter()
                    result = await asyncio.wait_for(execute(), timeout=timeout_seconds)
                    response = result.response.model_dump(mode="json", by_alias=True)
                    _write(directory / "result.json", response)
                    row.update(
                        status="completed",
                        elapsed_seconds=result.elapsed_seconds,
                        pipeline_cache_hit=result.pipeline_cache_hit,
                        fingerprint=result.manifest["fingerprint"],
                        metrics=measure(
                            response,
                            case,
                            pipeline_cache_hit=result.pipeline_cache_hit,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - preserve failed trials and continue the matrix
                    # Errors may include provider URLs/headers. Keep the type, not raw credentials.
                    row.update(
                        status="failed",
                        phase=phase,
                        error_type=type(exc).__name__,
                        elapsed_seconds=perf_counter() - started,
                    )
                _write(directory / "trial.json", row)
                trials.append(row)
                _write(output_dir / "summary.json", _summary(trials))
    report = _summary(trials)
    _write_trials_csv(output_dir / "trials.csv", trials)
    return report
