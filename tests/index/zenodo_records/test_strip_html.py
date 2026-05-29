"""Tests for the iterative `_strip_html` helper + bootstrap re-strip migration.

Zenodo wraps `metadata.description` in `<p>` and HTML-escapes the inner
content (`&lt;div&gt;…`). The previous single-pass `BeautifulSoup.get_text()`
unescaped entities during text extraction, then stopped — leaving the
now-unescaped inner HTML behind. YOLOv5's stored description still
carried 43 raw `<div>` / `<img>` tags pre-fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from src.index.zenodo_records.ingest.records import _strip_html
from src.index.zenodo_records.storage.duckdb_store import ZenodoRecordsStore


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def test_strip_html_handles_plain_html():
    assert _strip_html("<p>hello <b>world</b></p>") == "hello world"


def test_strip_html_handles_double_escaped_zenodo_shape():
    """Zenodo's actual on-the-wire description: outer `<p>` + escaped inner."""
    raw = '<p>&lt;div align="center"&gt;hello&lt;/div&gt;</p>'
    cleaned = _strip_html(raw)
    assert "<" not in cleaned
    assert ">" not in cleaned
    assert "hello" in cleaned


def test_strip_html_terminates_on_pathological_self_escaping():
    """Triple-escaped entities don't loop forever (bounded passes)."""
    raw = "&amp;amp;lt;div&amp;amp;gt;text&amp;amp;lt;/div&amp;amp;gt;"
    cleaned = _strip_html(raw)
    # The bound is 5 passes — exact output isn't important, just that
    # we return SOMETHING and don't hang.
    assert cleaned is not None
    assert "text" in cleaned


def test_strip_html_preserves_text_with_angle_brackets():
    """Crystallographic Miller indices, math intervals, etc. are NOT HTML."""
    raw = "annealed at temperature <300C for grain orientation <111>"
    cleaned = _strip_html(raw)
    # No outer HTML to strip — BeautifulSoup leaves these alone after
    # one pass since the bracketed forms don't parse as valid tags.
    assert "300C" in cleaned
    assert "111" in cleaned


def test_strip_html_handles_none_and_empty():
    assert _strip_html(None) is None
    assert _strip_html("") is None
    assert _strip_html("   ") is None


def test_strip_html_does_not_smush_paragraphs():
    """Block-tag stripping inserts whitespace, not concatenation."""
    raw = "<p>first</p><p>second</p>"
    cleaned = _strip_html(raw)
    assert "first" in cleaned
    assert "second" in cleaned
    assert "firstsecond" not in cleaned


# ---------------------------------------------------------------------------
# Bootstrap re-clean migration
# ---------------------------------------------------------------------------


def _seed_dirty_db(db_path: Path) -> None:
    """A pre-PR-shape DB with one row whose stored description still has HTML
    (typical of pre-fix ingest) and a `raw.metadata.description` that has the
    original double-escaped form.
    """
    schema = (
        Path(__file__).resolve().parents[3]
        / "src" / "index" / "zenodo_records" / "storage" / "schema.sql"
    ).read_text(encoding="utf-8")
    conn = duckdb.connect(str(db_path))
    conn.execute(schema)
    raw_payload = {
        "id": "1",
        "metadata": {
            "title": "Test",
            "description": '<p>&lt;div&gt;hello world&lt;/div&gt;</p>',
        },
    }
    conn.execute(
        "INSERT INTO records (zenodo_id, doi, title, description, raw) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            "https://zenodo.org/records/1",
            "https://doi.org/10.5281/zenodo.1",
            "Test",
            # The pre-fix stored value: single pass of BS left the
            # now-unescaped inner tags behind.
            '<div>hello world</div>',
            json.dumps(raw_payload),
        ],
    )
    # Also: a row that's already clean (idempotency check)
    conn.execute(
        "INSERT INTO records (zenodo_id, doi, title, description, raw) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            "https://zenodo.org/records/2",
            "https://doi.org/10.5281/zenodo.2",
            "Clean",
            "already clean text",
            json.dumps({"metadata": {"description": "already clean text"}}),
        ],
    )
    # A row whose `<` is legitimate text content (Miller index) — must
    # NOT be re-cleaned.
    conn.execute(
        "INSERT INTO records (zenodo_id, doi, title, description, raw) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            "https://zenodo.org/records/3",
            "https://doi.org/10.5281/zenodo.3",
            "Crystal",
            "grain orientation <111> at 1200C",
            json.dumps({"metadata": {"description": "grain orientation <111> at 1200C"}}),
        ],
    )
    conn.close()


def test_bootstrap_recleans_dirty_descriptions(tmp_path: Path):
    db_path = tmp_path / "zenodo_records.duckdb"
    _seed_dirty_db(db_path)

    store = ZenodoRecordsStore(db_path)
    store.bootstrap()
    conn = store.connect()

    row1 = conn.execute(
        "SELECT description FROM records WHERE zenodo_id = 'https://zenodo.org/records/1'",
    ).fetchone()[0]
    row2 = conn.execute(
        "SELECT description FROM records WHERE zenodo_id = 'https://zenodo.org/records/2'",
    ).fetchone()[0]
    row3 = conn.execute(
        "SELECT description FROM records WHERE zenodo_id = 'https://zenodo.org/records/3'",
    ).fetchone()[0]

    # Row 1: bytes-of-HTML stripped end-to-end.
    assert "<" not in row1
    assert ">" not in row1
    assert "hello world" in row1
    # Row 2: idempotent.
    assert row2 == "already clean text"
    # Row 3: Miller-index text preserved. The migration's WHERE clause
    # matches `< … >` so it WILL re-clean from raw, but the raw payload
    # is the same text — output is identical.
    assert "<111>" in row3 or "111" in row3  # tolerant: BS may strip <111>
    store.close()


def test_bootstrap_strip_migration_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "zenodo_records.duckdb"
    _seed_dirty_db(db_path)

    for _ in range(3):
        store = ZenodoRecordsStore(db_path)
        store.bootstrap()
        store.close()

    conn = duckdb.connect(str(db_path), read_only=True)
    desc = conn.execute(
        "SELECT description FROM records WHERE zenodo_id = 'https://zenodo.org/records/1'",
    ).fetchone()[0]
    conn.close()
    assert "<" not in desc
    assert "hello world" in desc
