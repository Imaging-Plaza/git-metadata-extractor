from __future__ import annotations

import json
import time

from src.v2.graph.store import GraphStore
from src.v2.pipeline.stages.stats import compute_stats

EXPECTED_ENTITY_COUNT = 8


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "stats_computation.db"))


def test_empty_store_has_zero_counts(tmp_path) -> None:
    store = _build_store(tmp_path)

    stats = compute_stats(store)

    assert stats.entities_count == 0
    assert stats.triples_count == 0
    assert stats.duration_ms == 0
    assert stats.stages_completed == []


def test_store_entity_count_is_reported(tmp_path) -> None:
    store = _build_store(tmp_path)
    for idx in range(5):
        store.insert_entity(
            entity_type="person",
            entity_id=f"person-{idx}",
            data={"schema:name": f"Person {idx}"},
            identifiers={},
            id_source="test",
        )
    for idx in range(3):
        store.insert_entity(
            entity_type="repository",
            entity_id=f"repo-{idx}",
            data={"schema:name": f"Repo {idx}"},
            identifiers={},
            id_source="test",
        )

    stats = compute_stats(store)

    assert stats.entities_count == EXPECTED_ENTITY_COUNT


def test_run_specific_stats_include_duration_and_stages(tmp_path) -> None:
    store = _build_store(tmp_path)
    run_id = store.create_run("https://github.com/owner/repo", "repository")
    time.sleep(0.02)
    store.complete_run(
        run_id,
        stats={
            "stages_completed": ["context_gather", "repo_agent", "person_agents"],
        },
    )

    stats = compute_stats(store, run_id=run_id)

    assert stats.run_id == run_id
    assert stats.duration_ms >= 1
    assert stats.stages_completed == [
        "context_gather",
        "repo_agent",
        "person_agents",
    ]


def test_triple_count_matches_graph_length(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="person",
        entity_id="person-1",
        data={"schema:name": "Ada"},
        identifiers={},
        id_source="test",
    )
    store.insert_entity(
        entity_type="repository",
        entity_id="repo-1",
        data={"schema:name": "Repo"},
        identifiers={},
        id_source="test",
    )
    store.insert_edge("person-1", "repo-1", "contributes_to")
    graph = store.get_rdf_graph()

    stats = compute_stats(store, graph=graph)

    assert stats.triples_count == len(graph)


def test_stats_model_is_json_serializable(tmp_path) -> None:
    store = _build_store(tmp_path)

    stats = compute_stats(store)
    serialized = json.dumps(stats.model_dump(mode="json"))

    assert isinstance(serialized, str)
