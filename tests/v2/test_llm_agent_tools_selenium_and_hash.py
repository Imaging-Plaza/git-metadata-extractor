from __future__ import annotations

from git_metadata_extractor.agents.llm.agent_tools.email_hash import hash_user_email
from git_metadata_extractor.agents.llm.agent_tools.selenium_fetch import fetch_link_content_via_selenium


def test_fetch_link_content_via_selenium_rejects_non_http_urls() -> None:
    result = fetch_link_content_via_selenium("urn:pulse:foo")
    assert result["fetched"] is False
    assert result["error"] == "Invalid http(s) URL"


def test_hash_user_email_hashes_local_part_and_keeps_domain() -> None:
    hashed = hash_user_email("alice@example.org")
    assert hashed == "2bd806c97f0e@example.org"


def test_hash_user_email_is_idempotent_for_pre_hashed_local_part() -> None:
    already_hashed = "2bd806c97f0e@example.org"
    assert hash_user_email(already_hashed) == already_hashed
