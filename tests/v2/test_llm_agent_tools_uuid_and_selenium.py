from __future__ import annotations

import uuid

from src.v2.agents.llm.agent_tools.selenium_fetch import fetch_link_content_via_selenium
from src.v2.agents.llm.agent_tools.uuid import generate_uuid_v4, generate_uuid_v4_batch


def test_generate_uuid_v4_returns_valid_uuid4() -> None:
    value = generate_uuid_v4()
    parsed = uuid.UUID(value)
    assert parsed.version == 4


def test_generate_uuid_v4_batch_respects_bounds_and_returns_uuid4_values() -> None:
    many = generate_uuid_v4_batch(500)
    assert len(many) == 100
    assert len(set(many)) == len(many)
    for value in many:
        parsed = uuid.UUID(value)
        assert parsed.version == 4

    minimum = generate_uuid_v4_batch(0)
    assert len(minimum) == 1
    assert uuid.UUID(minimum[0]).version == 4


def test_fetch_link_content_via_selenium_rejects_non_http_urls() -> None:
    result = fetch_link_content_via_selenium("urn:git-metadata-extractor:entity:foo")
    assert result["fetched"] is False
    assert result["error"] == "Invalid http(s) URL"
