from __future__ import annotations

from uuid import UUID

from git_metadata_extractor.agents.models import generate_uuid

UUID_VERSION_4 = 4


def test_generate_uuid_returns_uuid4_strings() -> None:
    generated = [generate_uuid() for _ in range(12)]

    assert len(generated) == len(set(generated))
    for value in generated:
        parsed = UUID(value)
        assert parsed.version == UUID_VERSION_4
