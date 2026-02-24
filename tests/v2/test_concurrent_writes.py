from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from src.v2.graph.concurrency import GraphStoreBusyError, with_write_retry
from src.v2.graph.store import GraphStore

DEFAULT_BUSY_TIMEOUT_MS = 5000
BUSY_ERROR_MESSAGE = "database is locked"
READER_SLEEP_SECONDS = 0.2
MAX_READER_ELAPSED_SECONDS = 0.45
WRITER_COMPLETION_TIMEOUT_SECONDS = 0.3
RETRY_SUCCESS_ATTEMPTS = 3
EXPECTED_BACKOFF_SEQUENCE = [0.05, 0.1]


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "concurrent_writes.db"))


def test_wal_mode_is_enabled(tmp_path) -> None:
    store = _build_store(tmp_path)

    with store._connect() as connection:  # noqa: SLF001
        journal_mode = str(connection.execute("PRAGMA journal_mode;").fetchone()[0]).lower()

    assert journal_mode == "wal"


def test_two_concurrent_readers_do_not_block_each_other(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="organization",
        entity_id="org-reader",
        data={"schema:name": "Reader Org"},
        identifiers={},
        id_source="uuid",
    )
    completed: list[str] = []

    def _reader(label: str) -> None:
        with store._connect() as connection:  # noqa: SLF001
            connection.execute("BEGIN;")
            connection.execute("SELECT COUNT(*) FROM entities;").fetchone()
            time.sleep(READER_SLEEP_SECONDS)
            connection.execute("SELECT COUNT(*) FROM entities;").fetchone()
            connection.execute("COMMIT;")
        completed.append(label)

    start = time.perf_counter()
    thread_a = threading.Thread(target=_reader, args=("a",), daemon=True)
    thread_b = threading.Thread(target=_reader, args=("b",), daemon=True)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=2)
    thread_b.join(timeout=2)
    elapsed = time.perf_counter() - start

    assert set(completed) == {"a", "b"}
    assert elapsed < MAX_READER_ELAPSED_SECONDS


def test_write_during_read_does_not_block_reader(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="organization",
        entity_id="org-existing",
        data={"schema:name": "Existing Org"},
        identifiers={},
        id_source="uuid",
    )

    reader_holding_lock = threading.Event()
    release_reader = threading.Event()
    writer_done = threading.Event()

    def _reader() -> None:
        with store._connect() as connection:  # noqa: SLF001
            connection.execute("BEGIN;")
            connection.execute("SELECT * FROM entities WHERE id = ?;", ("org-existing",)).fetchall()
            reader_holding_lock.set()
            release_reader.wait(timeout=2)
            connection.execute("COMMIT;")

    def _writer() -> None:
        reader_holding_lock.wait(timeout=2)
        store.insert_entity(
            entity_type="organization",
            entity_id="org-new",
            data={"schema:name": "Inserted During Read"},
            identifiers={},
            id_source="uuid",
        )
        writer_done.set()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    writer_thread = threading.Thread(target=_writer, daemon=True)
    reader_thread.start()
    writer_thread.start()

    assert writer_done.wait(timeout=WRITER_COMPLETION_TIMEOUT_SECONDS) is True
    release_reader.set()
    reader_thread.join(timeout=2)
    writer_thread.join(timeout=2)
    assert store.get_entity("org-new") is not None


def test_simulated_busy_condition_retries_and_succeeds() -> None:
    attempts = {"count": 0}

    @with_write_retry(max_retries=3, backoff_base=0.001)
    def _flaky_write() -> str:
        attempts["count"] += 1
        if attempts["count"] < RETRY_SUCCESS_ATTEMPTS:
            raise sqlite3.OperationalError(BUSY_ERROR_MESSAGE)
        return "ok"

    assert _flaky_write() == "ok"
    assert attempts["count"] == RETRY_SUCCESS_ATTEMPTS


def test_busy_timeout_and_backoff_are_configured(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _build_store(tmp_path)
    sleep_calls: list[float] = []
    monkeypatch.setattr("src.v2.graph.concurrency.time.sleep", sleep_calls.append)

    with store._connect() as connection:  # noqa: SLF001
        busy_timeout = int(connection.execute("PRAGMA busy_timeout;").fetchone()[0])

    attempts = {"count": 0}

    @with_write_retry(max_retries=2, backoff_base=0.05)
    def _flaky_write() -> None:
        attempts["count"] += 1
        if attempts["count"] < RETRY_SUCCESS_ATTEMPTS:
            raise sqlite3.OperationalError(BUSY_ERROR_MESSAGE)

    _flaky_write()

    assert busy_timeout == DEFAULT_BUSY_TIMEOUT_MS
    assert sleep_calls == EXPECTED_BACKOFF_SEQUENCE


def test_exhausted_retries_raise_graph_store_busy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.v2.graph.concurrency.time.sleep", lambda _seconds: None)

    @with_write_retry(max_retries=2, backoff_base=0.0)
    def _always_busy() -> None:
        raise sqlite3.OperationalError(BUSY_ERROR_MESSAGE)

    with pytest.raises(GraphStoreBusyError, match="remained busy"):
        _always_busy()
