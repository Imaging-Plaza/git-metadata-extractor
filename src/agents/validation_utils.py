"""
Validation Utilities

HTML retrieval and URL normalization functions for validation.
"""

import asyncio
import logging
import os
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

# Semaphore to limit concurrent Selenium sessions (shared with other agents)
_MAX_SELENIUM_SESSIONS = int(os.getenv("MAX_SELENIUM_SESSIONS", "1"))
_selenium_semaphore = asyncio.Semaphore(_MAX_SELENIUM_SESSIONS)


async def fetch_html_content(url: str, use_selenium: bool = True) -> str:
    """
    Fetch HTML content from a URL, using Selenium first, then falling back to httpx.

    Args:
        url: URL to fetch
        use_selenium: Whether to try Selenium first (default: True)

    Returns:
        Markdown-formatted content (HTML converted to markdown, scripts and styles removed)
    """
    selenium_url = os.getenv(
        "SELENIUM_REMOTE_URL",
        "http://selenium-standalone-firefox:4444",
    )

    # Try Selenium first if requested
    if use_selenium:
        try:
            async with _selenium_semaphore:
                logger.debug(f"🔒 Acquired Selenium semaphore for URL: '{url}'")

                options = Options()
                options.add_argument("--headless")
                options.set_preference(
                    "general.useragent.override",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                )

                driver = None
                try:
                    driver = webdriver.Remote(
                        command_executor=selenium_url,
                        options=options,
                    )

                    driver.get(url)

                    # Wait for page to load
                    WebDriverWait(driver, 30).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body")),
                    )

                    # Get page source
                    html_content = driver.page_source

                    # Clean HTML
                    soup = BeautifulSoup(html_content, "html.parser")
                    # Remove scripts and styles
                    for script in soup(["script", "style"]):
                        script.decompose()
                    # Convert to markdown to preserve structure
                    markdown_content = md(str(soup), heading_style="ATX", bullets="-")

                    logger.info(
                        f"✓ Fetched HTML content from {url} using Selenium (converted to markdown)",
                    )
                    return markdown_content

                finally:
                    if driver:
                        try:
                            driver.quit()
                        except Exception:
                            pass
                    logger.debug(f"🔓 Released Selenium semaphore for URL: '{url}'")

        except Exception as e:
            logger.warning(
                f"Selenium failed for {url}, falling back to httpx: {e}",
            )
            # Fall through to httpx

    # Fallback to httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()

            # Clean HTML
            soup = BeautifulSoup(response.text, "html.parser")
            # Remove scripts and styles
            for script in soup(["script", "style"]):
                script.decompose()
            # Convert to markdown to preserve structure
            markdown_content = md(str(soup), heading_style="ATX", bullets="-")

            logger.info(
                f"✓ Fetched HTML content from {url} using httpx (converted to markdown)",
            )
            return markdown_content

    except Exception as e:
        logger.error(f"Failed to fetch HTML content from {url}: {e}")
        raise


def normalize_infoscience_url(url_or_uuid: str, entity_type: str) -> Optional[str]:
    """
    Normalize an Infoscience URL or UUID to proper format.

    Args:
        url_or_uuid: URL or UUID string
        entity_type: Type of entity ("publication", "person", "orgunit")

    Returns:
        Normalized URL or None if invalid
    """
    if not url_or_uuid:
        return None

    # Extract UUID if it's in a URL
    uuid_pattern = r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    uuid_match = re.search(uuid_pattern, url_or_uuid, re.IGNORECASE)

    if uuid_match:
        uuid = uuid_match.group(1)
    else:
        # Check if it's just a UUID
        if re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            url_or_uuid,
            re.IGNORECASE,
        ):
            uuid = url_or_uuid
        else:
            # Not a valid UUID format
            logger.warning(f"Could not extract UUID from: {url_or_uuid}")
            return None

    # Build normalized URL based on entity type
    if entity_type == "publication":
        # Publications can use either /record/{handle} or /entities/publication/{uuid}
        # Prefer /entities/publication/{uuid} for consistency
        return f"https://infoscience.epfl.ch/entities/publication/{uuid}"
    elif entity_type == "person":
        return f"https://infoscience.epfl.ch/entities/person/{uuid}"
    elif entity_type == "orgunit":
        return f"https://infoscience.epfl.ch/entities/orgunit/{uuid}"
    else:
        logger.warning(f"Unknown entity type for normalization: {entity_type}")
        return None


def normalize_infoscience_publication_url(url_or_uuid: str) -> Optional[str]:
    """Normalize an Infoscience publication URL."""
    return normalize_infoscience_url(url_or_uuid, "publication")


def normalize_infoscience_author_url(url_or_uuid: str) -> Optional[str]:
    """Normalize an Infoscience author/person URL."""
    return normalize_infoscience_url(url_or_uuid, "person")


def normalize_infoscience_lab_url(url_or_uuid: str) -> Optional[str]:
    """Normalize an Infoscience lab/orgunit URL."""
    return normalize_infoscience_url(url_or_uuid, "orgunit")
