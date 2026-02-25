from __future__ import annotations

from src.v2.graph.store import GraphStore
from src.v2.pipeline.stages.intermediates import assemble_intermediates

SOURCE_URL = "https://github.com/owner/repo"
EXPECTED_TWO_INTERMEDIATES = 2
LIMIT_TWO = 2


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "intermediates_envelope.db"))


def _create_run(store: GraphStore, source_url: str = SOURCE_URL) -> str:
    return store.create_run(source_url, "repository")


def _insert_intermediate(
    store: GraphStore,
    row: dict[str, str | dict[str, int]],
) -> None:
    run_id = row.get("run_id")
    store.insert_intermediate(
        source_url=str(row.get("source_url", SOURCE_URL)),
        agent_name=str(row["agent_name"]),
        run_id=str(run_id) if isinstance(run_id, str) else None,
        data=row["data"],
        intermediate_id=str(row["id"]),
        created_at=str(row["timestamp"]),
    )


def test_returns_intermediates_grouped_by_agent_name(tmp_path) -> None:
    store = _build_store(tmp_path)
    run_id = _create_run(store)
    _insert_intermediate(
        store,
        {
            "id": "a-1",
            "agent_name": "person_agent",
            "run_id": run_id,
            "timestamp": "2026-02-24T10:00:00Z",
            "data": {"idx": 1},
        },
    )
    _insert_intermediate(
        store,
        {
            "id": "b-1",
            "agent_name": "repo_agent",
            "run_id": run_id,
            "timestamp": "2026-02-24T10:01:00Z",
            "data": {"idx": 2},
        },
    )

    envelopes = assemble_intermediates(SOURCE_URL, store)

    assert len(envelopes) == EXPECTED_TWO_INTERMEDIATES
    assert {envelope.agent_name for envelope in envelopes} == {"person_agent", "repo_agent"}


def test_each_envelope_has_required_fields(tmp_path) -> None:
    store = _build_store(tmp_path)
    run_id = _create_run(store)
    _insert_intermediate(
        store,
        {
            "id": "a-1",
            "agent_name": "person_agent",
            "run_id": run_id,
            "timestamp": "2026-02-24T10:00:00Z",
            "data": {"idx": 1},
        },
    )

    envelope = assemble_intermediates(SOURCE_URL, store)[0]

    assert envelope.agent_name == "person_agent"
    assert envelope.run_id == run_id
    assert envelope.timestamp == "2026-02-24T10:00:00Z"
    assert envelope.data == {"idx": 1}


def test_limit_caps_total_intermediates_returned(tmp_path) -> None:
    store = _build_store(tmp_path)
    run_id = _create_run(store)
    for idx in range(4):
        _insert_intermediate(
            store,
            {
                "id": f"a-{idx}",
                "agent_name": "person_agent",
                "run_id": run_id,
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
    run_id = _create_run(store)
    _insert_intermediate(
        store,
        {
            "id": "p-older",
            "agent_name": "person_agent",
            "run_id": run_id,
            "timestamp": "2026-02-24T10:00:00Z",
            "data": {"idx": 1},
        },
    )
    _insert_intermediate(
        store,
        {
            "id": "r-middle",
            "agent_name": "repo_agent",
            "run_id": run_id,
            "timestamp": "2026-02-24T10:01:00Z",
            "data": {"idx": 2},
        },
    )
    _insert_intermediate(
        store,
        {
            "id": "p-newest",
            "agent_name": "person_agent",
            "run_id": run_id,
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


def test_graphstore_intermediates_support_optional_run_filter(tmp_path) -> None:
    store = _build_store(tmp_path)
    run_id_1 = _create_run(store)
    run_id_2 = _create_run(store)
    _insert_intermediate(
        store,
        {
            "id": "run-1-item",
            "agent_name": "person_agent",
            "run_id": run_id_1,
            "timestamp": "2026-02-24T10:00:00Z",
            "data": {"idx": 1},
        },
    )
    _insert_intermediate(
        store,
        {
            "id": "run-2-item",
            "agent_name": "person_agent",
            "run_id": run_id_2,
            "timestamp": "2026-02-24T10:01:00Z",
            "data": {"idx": 2},
        },
    )

    rows = store.get_intermediates(
        source_url=SOURCE_URL,
        run_id=run_id_1,
        limit=None,
    )

    assert len(rows) == 1
    assert rows[0]["id"] == "run-1-item"
    assert rows[0]["run_id"] == run_id_1


def test_stage_reads_intermediates_through_graphstore_api(
    monkeypatch,
    tmp_path,
) -> None:
    store = _build_store(tmp_path)
    observed: dict[str, str | int | None] = {}

    def _get_intermediates(
        *,
        source_url: str | None,
        run_id: str | None,
        limit: int | None,
    ) -> list[dict[str, object]]:
        observed["source_url"] = source_url
        observed["run_id"] = run_id
        observed["limit"] = limit
        return [
            {
                "agent_name": "repo_agent",
                "run_id": "run-1",
                "created_at": "2026-02-24T10:00:00Z",
                "data": {"idx": 7},
            },
        ]

    monkeypatch.setattr(store, "get_intermediates", _get_intermediates)

    envelopes = assemble_intermediates(
        SOURCE_URL,
        store,
        limit=1,
        run_id="run-1",
    )

    assert observed == {
        "source_url": SOURCE_URL,
        "run_id": "run-1",
        "limit": 1,
    }
    assert len(envelopes) == 1
    assert envelopes[0].agent_name == "repo_agent"
    assert envelopes[0].run_id == "run-1"
    assert envelopes[0].data == {"idx": 7}
