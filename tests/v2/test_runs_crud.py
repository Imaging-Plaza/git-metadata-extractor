from __future__ import annotations

import time
from uuid import UUID

from src.v2.graph.store import GraphStore

EXPECTED_STAGES_COMPLETED = 4
EXPECTED_ENTITIES_EXTRACTED = 12
EXPECTED_DURATION_MS = 3800


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "runs_crud.db"))


def test_create_run_returns_uuid_and_initial_metadata(tmp_path) -> None:
    store = _build_store(tmp_path)

    run_id = store.create_run(
        source_url="https://github.com/Imaging-Plaza/git-metadata-extractor",
        detected_type="repository",
    )
    run = store.get_run(run_id)

    assert str(UUID(run_id)) == run_id
    assert run is not None
    assert run.status == "running"
    assert run.source_url == "https://github.com/Imaging-Plaza/git-metadata-extractor"
    assert run.detected_type == "repository"
    assert run.started_at is not None
    assert run.completed_at is None


def test_complete_run_sets_completed_status_stats_and_timestamp(tmp_path) -> None:
    store = _build_store(tmp_path)
    run_id = store.create_run("https://github.com/acme/project", "repository")

    completed = store.complete_run(
        run_id,
        stats={
            "stages_completed": EXPECTED_STAGES_COMPLETED,
            "entities_extracted": EXPECTED_ENTITIES_EXTRACTED,
            "duration_ms": EXPECTED_DURATION_MS,
        },
    )
    run = store.get_run(run_id)

    assert completed is True
    assert run is not None
    assert run.status == "completed"
    assert run.stats["stages_completed"] == EXPECTED_STAGES_COMPLETED
    assert run.stats["entities_extracted"] == EXPECTED_ENTITIES_EXTRACTED
    assert run.stats["duration_ms"] == EXPECTED_DURATION_MS
    assert run.completed_at is not None


def test_fail_run_sets_failed_status_and_error_detail(tmp_path) -> None:
    store = _build_store(tmp_path)
    run_id = store.create_run("https://github.com/acme/project", "repository")

    failed = store.fail_run(run_id, "provider timeout")
    run = store.get_run(run_id)

    assert failed is True
    assert run is not None
    assert run.status == "failed"
    assert run.error_detail == "provider timeout"
    assert run.completed_at is not None


def test_get_run_returns_full_record(tmp_path) -> None:
    store = _build_store(tmp_path)
    run_id = store.create_run("https://github.com/acme/project", "repository")
    store.complete_run(
        run_id,
        stats={
            "stages_completed": 2,
            "entities_extracted": 3,
            "duration_ms": 42,
        },
    )

    run = store.get_run(run_id)

    assert run is not None
    assert run.id == run_id
    assert run.source_url == "https://github.com/acme/project"
    assert run.detected_type == "repository"
    assert run.status == "completed"
    assert run.stats == {
        "stages_completed": 2,
        "entities_extracted": 3,
        "duration_ms": 42,
    }
    assert run.started_at is not None
    assert run.completed_at is not None


def test_get_runs_by_source_returns_newest_first(tmp_path) -> None:
    store = _build_store(tmp_path)
    source_url = "https://github.com/acme/project"

    first = store.create_run(source_url, "repository")
    time.sleep(0.01)
    second = store.create_run(source_url, "repository")
    time.sleep(0.01)
    third = store.create_run(source_url, "repository")

    runs = store.get_runs_by_source(source_url, limit=2)

    assert [run.id for run in runs] == [third, second]
    assert all(run.source_url == source_url for run in runs)
    assert first not in {run.id for run in runs}
