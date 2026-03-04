from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic_ai import Tool
from selenium import webdriver
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

DEFAULT_SELENIUM_TIMEOUT_SECONDS = 30
DEFAULT_WAIT_SECONDS = 8
DEFAULT_MAX_CHARS = 4000
MAX_ALLOWED_CHARS = 20000


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


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
