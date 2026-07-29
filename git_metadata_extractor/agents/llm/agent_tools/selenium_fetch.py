from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic_ai import Tool
from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support.ui import WebDriverWait

from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

DEFAULT_SELENIUM_TIMEOUT_SECONDS = 30
DEFAULT_WAIT_SECONDS = 8
DEFAULT_MAX_CHARS = 4000
MAX_ALLOWED_CHARS = 20000


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject internal/special-use addresses (incl. the cloud metadata
    endpoint 169.254.169.254, loopback, RFC1918, link-local, reserved)."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_safe_public_url(value: str) -> bool:
    """SSRF guard: the host must resolve only to public IPs.

    The Selenium tool fetches links the LLM extracted from user-supplied
    repo/profile content, so without this an attacker could point it at
    internal services or the cloud metadata endpoint. Fails closed: an
    unresolvable host or any internal-resolving IP is rejected. (DNS
    rebinding between this check and the fetch is a residual risk; binding
    the resolved IP would be the stronger mitigation.)
    """
    host = urlparse(value).hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if _is_blocked_ip(ip):
            return False
    return bool(infos)


def _load_html_via_selenium(
    url: str,
    *,
    timeout_seconds: int,
    wait_seconds: int,
) -> tuple[str, str, str]:
    selenium_remote_url = os.getenv("SELENIUM_REMOTE_URL", "").strip()
    if not selenium_remote_url:
        message = "Missing SELENIUM_REMOTE_URL"
        raise RuntimeError(message)

    options = FirefoxOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Remote(
        command_executor=selenium_remote_url,
        options=options,
    )
    try:
        driver.set_page_load_timeout(timeout_seconds)
        driver.get(url)
        WebDriverWait(driver, wait_seconds).until(
            lambda current_driver: current_driver.execute_script(
                "return document.readyState",
            )
            == "complete",
        )
        return driver.page_source or "", driver.current_url or url, driver.title or ""
    finally:
        driver.quit()


def fetch_link_content_via_selenium(
    url: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Fetch rendered page content from a URL via Selenium Grid."""

    logger.info("tool call: fetch_link_content_via_selenium — url=%r", url)
    record_query(service="selenium.fetch_link_content", query=str(url) if url else "")
    normalized_url = url.strip() if isinstance(url, str) else ""
    if not normalized_url or not _is_http_url(normalized_url):
        return {
            "url": normalized_url or None,
            "fetched": False,
            "final_url": None,
            "title": None,
            "text_excerpt": None,
            "content_length": 0,
            "error": "Invalid http(s) URL",
        }
    if not _is_safe_public_url(normalized_url):
        # SSRF protection: refuse internal / metadata / unresolvable hosts.
        return {
            "url": normalized_url,
            "fetched": False,
            "final_url": None,
            "title": None,
            "text_excerpt": None,
            "content_length": 0,
            "error": "URL host is not allowed (SSRF protection)",
        }

    bounded_max_chars = max(1, min(int(max_chars), MAX_ALLOWED_CHARS))
    try:
        html, final_url, title = _load_html_via_selenium(
            normalized_url,
            timeout_seconds=DEFAULT_SELENIUM_TIMEOUT_SECONDS,
            wait_seconds=DEFAULT_WAIT_SECONDS,
        )
        text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
        text_excerpt = text[:bounded_max_chars] if text else ""
        return {
            "url": normalized_url,
            "fetched": True,
            "final_url": final_url,
            "title": title,
            "text_excerpt": text_excerpt,
            "content_length": len(text),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fetch_link_content_via_selenium failed for %s: %s",
            normalized_url,
            exc,
        )
        return {
            "url": normalized_url,
            "fetched": False,
            "final_url": None,
            "title": None,
            "text_excerpt": None,
            "content_length": 0,
            "error": str(exc),
        }


fetch_link_content_via_selenium_tool = Tool(
    fetch_link_content_via_selenium,
    name="fetch_link_content_via_selenium",
    description=(
        "Fetch rendered page content from an http(s) URL using Selenium Grid "
        "(SELENIUM_REMOTE_URL). Returns fetch status, final URL, title, and page text excerpt."
    ),
)


def make_fetch_link_content_tool(cache: ProviderCache | None = None) -> Tool:
    """Return a Selenium-fetch tool optionally backed by a `ProviderCache`.

    When `cache` is None, returns the cacheless module-level singleton.
    Otherwise wraps the fetch in a cache lookup; only successful fetches
    (`fetched=True`) are stored, so transient errors retry next time.
    """
    if cache is None:
        return fetch_link_content_via_selenium_tool

    def _cached_fetch(
        url: str,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> dict[str, Any]:
        normalized_url = url.strip() if isinstance(url, str) else ""
        if not normalized_url or not _is_http_url(normalized_url):
            return fetch_link_content_via_selenium(url, max_chars)

        bounded_max_chars = max(1, min(int(max_chars), MAX_ALLOWED_CHARS))
        key = ProviderCache.make_key(
            "selenium",
            "fetch_link_content_via_selenium",
            url=normalized_url,
            max_chars=bounded_max_chars,
        )
        cached = cache.get(key)
        if cached is not None:
            logger.info(
                "fetch_link_content_via_selenium cache hit — url=%r",
                normalized_url,
            )
            return cached

        result = fetch_link_content_via_selenium(url, max_chars)
        if isinstance(result, dict) and result.get("fetched"):
            cache.set(key, result)
        return result

    return Tool(
        _cached_fetch,
        name="fetch_link_content_via_selenium",
        description=(
            "Fetch rendered page content from an http(s) URL using Selenium Grid "
            "(SELENIUM_REMOTE_URL). Returns fetch status, final URL, title, and page text excerpt."
        ),
    )
