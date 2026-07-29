# tests/v2/test_selenium_ssrf.py
"""Audit finding ssrf-selenium-fetch: the Selenium link fetcher accepted any
http(s) URL (incl. internal hosts and the 169.254.169.254 cloud-metadata
endpoint). It now resolves the host and rejects internal/special-use IPs.
"""
from __future__ import annotations

import pytest

from git_metadata_extractor.agents.llm.agent_tools.selenium_fetch import (
    _is_safe_public_url,
    fetch_link_content_via_selenium,
)

BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",  # AWS/GCP metadata
    "http://127.0.0.1:8000/",                      # loopback
    "http://10.0.0.5/internal",                    # RFC1918
    "http://192.168.1.1/",                          # RFC1918
    "http://[::1]/",                                # IPv6 loopback
    "http://0.0.0.0/",                              # unspecified
]


@pytest.mark.parametrize("url", BLOCKED)
def test_internal_urls_are_blocked(url):
    assert _is_safe_public_url(url) is False


def test_public_ip_is_allowed():
    # numeric public IP — getaddrinfo returns it without a DNS lookup
    assert _is_safe_public_url("http://93.184.216.34/") is True


def test_non_http_or_hostless_is_rejected():
    assert _is_safe_public_url("file:///etc/passwd") is False
    assert _is_safe_public_url("not a url") is False


def test_fetch_refuses_metadata_endpoint_without_touching_selenium():
    # No SELENIUM_REMOTE_URL configured: a vulnerable build would still try to
    # reach Selenium; the guard must short-circuit with an SSRF error first.
    result = fetch_link_content_via_selenium("http://169.254.169.254/latest/meta-data/")
    assert result["fetched"] is False
    assert "SSRF" in (result["error"] or "")
