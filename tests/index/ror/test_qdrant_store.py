from __future__ import annotations

import uuid

from src.index.ror.qdrant_store import _stable_point_id


def test_stable_point_id_is_deterministic():
    a = _stable_point_id("https://ror.org/02s376052")
    b = _stable_point_id("https://ror.org/02s376052")
    assert a == b


def test_stable_point_id_different_for_different_rors():
    a = _stable_point_id("https://ror.org/02s376052")
    b = _stable_point_id("https://ror.org/05a28rw58")
    assert a != b


def test_stable_point_id_is_valid_uuid():
    raw = _stable_point_id("https://ror.org/02s376052")
    parsed = uuid.UUID(raw)
    assert parsed.version == 5
