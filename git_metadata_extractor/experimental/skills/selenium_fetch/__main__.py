"""CLI entry point for the `selenium_fetch` skill.

Wraps :func:`git_metadata_extractor.agents.llm.agent_tools.selenium_fetch.fetch_link_content_via_selenium`.
Renders a page in headless Firefox via Selenium Grid, returns the
extracted text excerpt + metadata. The underlying helper is synchronous
so we run it directly without `asyncio.run`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from git_metadata_extractor.agents.llm.agent_tools.selenium_fetch import (
    DEFAULT_MAX_CHARS,
    MAX_ALLOWED_CHARS,
    fetch_link_content_via_selenium,
)
from git_metadata_extractor.experimental.skills._runtime import SkillError, emit_error, emit_success

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gme-selenium-fetch",
        description="Render an http(s) URL in headless Firefox and return its text excerpt.",
    )
    parser.add_argument("url", help="The http(s) URL to fetch.")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=(
            f"Maximum characters of body text to return "
            f"(default: {DEFAULT_MAX_CHARS}, hard cap: {MAX_ALLOWED_CHARS})."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Synchronous entrypoint — Selenium driver is sync."""
    level_name = os.environ.get("V2_SKILL_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not os.environ.get("SELENIUM_REMOTE_URL", "").strip():
        emit_error(
            SkillError(
                "Missing SELENIUM_REMOTE_URL — Selenium Grid endpoint is required.",
                kind="provider_unavailable",
            )
        )
        return 1
    try:
        result = fetch_link_content_via_selenium(args.url, max_chars=args.max_chars)
    except KeyboardInterrupt:
        emit_error(SkillError("interrupted", kind="interrupted"))
        return 130
    except Exception as err:  # noqa: BLE001
        logger.exception("selenium fetch failed")
        emit_error(err)
        return 2
    emit_success(result)
    return 0 if result.get("fetched") else 3


if __name__ == "__main__":
    sys.exit(main())
