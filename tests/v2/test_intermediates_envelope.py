from __future__ import annotations

import json
import sqlite3

from src.v2.graph.store import GraphStore
from src.v2.pipeline.stages.intermediates import assemble_intermediates

SOURCE_URL = "https://github.com/owner/repo"
EXPECTED_TWO_INTERMEDIATES = 2
LIMIT_TWO = 2


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "intermediates_envelope.db"))


def _insert_intermediate(
    store: GraphStore,
    row: dict[str, str | dict[str, int]],
) -> None:
    db_path = store._db_path  # noqa: SLF001
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO intermediates (
                id,
                source_url,
                agent_name,
                run_id,
                data,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                str(row["id"]),
                str(row.get("source_url", SOURCE_URL)),
                str(row["agent_name"]),
                str(row["run_id"]),
                json.dumps(row["data"]),
                str(row["timestamp"]),
            ),
        )
        connection.commit()


def test_returns_intermediates_grouped_by_agent_name(tmp_path) -> None:
    store = _build_store(tmp_path)
    _insert_intermediate(
        store,
        {
            "id": "a-1",
            "agent_name": "person_agent",
            "run_id": "run-1",
            "timestamp": "2026-02-24T10:00:00Z",
            "data": {"idx": 1},
        },
    )
    _insert_intermediate(
        store,
        {
            "id": "b-1",
            "agent_name": "repo_agent",
            "run_id": "run-1",
            "timestamp": "2026-02-24T10:01:00Z",
            "data": {"idx": 2},
        },
    )

    envelopes = assemble_intermediates(SOURCE_URL, store)

    assert len(envelopes) == EXPECTED_TWO_INTERMEDIATES
    assert {envelope.agent_name for envelope in envelopes} == {"person_agent", "repo_agent"}


def test_each_envelope_has_required_fields(tmp_path) -> None:
    store = _build_store(tmp_path)
    _insert_intermediate(
        store,
        {
            "id": "a-1",
            "agent_name": "person_agent",
            "run_id": "run-1",
            "timestamp": "2026-02-24T10:00:00Z",
            "data": {"idx": 1},
        },
    )

    envelope = assemble_intermediates(SOURCE_URL, store)[0]

    assert envelope.agent_name == "person_agent"
    assert envelope.run_id == "run-1"
    assert envelope.timestamp == "2026-02-24T10:00:00Z"
    assert envelope.data == {"idx": 1}


def test_limit_caps_total_intermediates_returned(tmp_path) -> None:
    store = _build_store(tmp_path)
    for idx in range(4):
        _insert_intermediate(
            store,
            {
                "id": f"a-{idx}",
                "agent_name": "person_agent",
                "run_id": "run-1",
                "timestamp": f"2026-02-24T10:0{idx}:00Z",
                "data": {"idx": idx},
            },
        )

    envelopes = assemble_intermediates(SOURCE_URL, store, limit=LIMIT_TWO)

    assert len(envelopes) == EXPECTED_TWO_INTERMEDIATES


def test_no_intermediates_returns_empty_list(tmp_path) -> None:
    store = _build_store(tmp_path)

    assert assemble_intermediates(SOURCE_URL, store) == []


def test_multiple_agents_are_interleaved_by_timestamp(tmp_path) -> None:
    store = _build_store(tmp_path)
    _insert_intermediate(
        store,
        {
            "id": "p-older",
            "agent_name": "person_agent",
            "run_id": "run-1",
            "timestamp": "2026-02-24T10:00:00Z",
            "data": {"idx": 1},
        },
    )
    _insert_intermediate(
        store,
        {
            "id": "r-middle",
            "agent_name": "repo_agent",
            "run_id": "run-1",
            "timestamp": "2026-02-24T10:01:00Z",
            "data": {"idx": 2},
        },
    )
    _insert_intermediate(
        store,
        {
            "id": "p-newest",
            "agent_name": "person_agent",
            "run_id": "run-1",
            "timestamp": "2026-02-24T10:02:00Z",
            "data": {"idx": 3},
        },
    )

    envelopes = assemble_intermediates(SOURCE_URL, store)

    assert [envelope.agent_name for envelope in envelopes] == [
        "person_agent",
        "repo_agent",
        "person_agent",
    ]
