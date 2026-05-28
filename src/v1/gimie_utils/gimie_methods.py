"""
GIMIE integration.

GIMIE 0.7.x mishandles two GitHub edge cases for brand-new / empty repositories:

- ``GET /repos/{owner}/{repo}/contributors`` returns **204 No Content** with an empty
  body when there are no commits; ``send_rest_query`` then calls ``resp.json()`` and
  raises ``JSONDecodeError`` (surfacing as HTTP 400 via the API ValueError handler).
- GraphQL ``repository.object(expression: \"HEAD:\")`` is **null** when there is no
  tree; ``list_files`` then does ``None[\"entries\"]`` and raises ``TypeError``.

- **CFF calendar dates:** PyYAML's YAML 1.1 timestamp constructor parses unquoted
  ``YYYY-M-D`` tokens on ``date-released`` / ``date-published`` / ``date-last-released``.
  Invalid calendars (e.g. ``2025-28-04`` day/month swap, or template junk like
  ``2025-13-14`` where neither order is valid) raise ``ValueError``. We rewrite those
  lines before parsing: fix obvious month/day swaps when possible, otherwise quote
  the token so YAML loads a string (GIMIE does not consume these date fields today).

We patch those behaviors before importing ``Project`` so all ``Project`` instances
see the fixes.
"""

from __future__ import annotations

import calendar
import contextlib
import json
import logging
import os
import re
from http import HTTPStatus
from typing import Any

import gimie.extractors.github as gimie_github
import requests
from gimie.extractors.github import GithubExtractor
from gimie.parsers.cff import CffParser

logger = logging.getLogger(__name__)

_MAX_MONTH = 12  # Gregorian calendar

_CFF_DATE_KEYS = (
    "date-released",
    "date-published",
    "date-last-released",
)
_CFF_DATE_LINE = re.compile(
    r"^(\s*)([a-zA-Z0-9_-]+)\s*:\s*(.*?)\s*$",
    re.MULTILINE,
)


def _calendar_date_ok(year: int, month: int, day: int) -> bool:
    if not (1 <= month <= _MAX_MONTH) or day < 1:
        return False
    last = calendar.monthrange(year, month)[1]
    return day <= last


def _fix_yyyy_mm_dd_triple(
    year: int, middle: int, last: int,
) -> tuple[int, int, int] | None:
    """If ``Y-m-d`` is invalid but ``Y-d-m`` is valid, treat middle/last as swapped."""
    if _calendar_date_ok(year, middle, last):
        return (year, middle, last)
    if _calendar_date_ok(year, last, middle):
        return (year, last, middle)
    return None


def _rewrite_cff_known_date_value(value_part: str) -> tuple[str, str] | None:
    """Return ``(new_rhs, reason)`` for a CFF date key's value, or None to keep as-is.

    PyYAML only auto-parses *unquoted* ``YYYY-M-D``. Quoting forces a string and avoids
    ``construct_yaml_timestamp`` (and GIMIE's CFF parser ignores these keys anyway).
    """
    trimmed = value_part.strip()
    if not trimmed:
        return None
    if (trimmed.startswith('"') and trimmed.endswith('"')) or (
        trimmed.startswith("'") and trimmed.endswith("'")
    ):
        return None

    token = trimmed.strip('"').strip("'")
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", token)
    if not m:
        return None

    y, a, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    fixed = _fix_yyyy_mm_dd_triple(y, a, b)
    if fixed is not None:
        fy, fm, fd = fixed
        iso = f"{fy:04d}-{fm:02d}-{fd:02d}"
        if (fy, fm, fd) != (y, a, b):
            return (iso, "normalized swapped day/month")
        return None

    escaped = token.replace("\\", "\\\\").replace('"', '\\"')
    return (f'"{escaped}"', "quoted invalid calendar (YAML timestamp escape)")


def _normalize_cff_calendar_dates(data: bytes) -> bytes:
    """Sanitize CFF ``date-*`` fields so PyYAML does not raise on bogus templates."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data

    def repl(match: re.Match[str]) -> str:
        indent, key, rest = match.group(1), match.group(2), match.group(3)
        if key.lower() not in _CFF_DATE_KEYS:
            return match.group(0)
        value_part, sep, comment = rest.partition("#")
        rewritten = _rewrite_cff_known_date_value(value_part)
        if rewritten is None:
            return match.group(0)
        new_rhs, reason = rewritten
        logger.info(
            "GIMIE CFF: %s %s from %r -> %r",
            reason,
            key,
            value_part.strip(),
            new_rhs,
        )
        suffix = f" {sep}{comment}" if sep else ""
        return f"{indent}{key}: {new_rhs}{suffix}"

    new_text = _CFF_DATE_LINE.sub(repl, text)
    return new_text.encode("utf-8")


_original_cff_parse = CffParser.parse


def _patched_cff_parse(self: CffParser, data: bytes) -> Any:
    return _original_cff_parse(self, _normalize_cff_calendar_dates(data))


CffParser.parse = _patched_cff_parse  # type: ignore[method-assign]

_original_send_rest_query = gimie_github.send_rest_query
_original_list_files = GithubExtractor.list_files


def _patched_send_rest_query(
    api: str,
    query: str,
    headers: dict[str, str],
) -> list[dict[str, Any]] | dict[str, Any]:
    """Treat GitHub 204 (empty body) as an empty JSON list (e.g. no contributors)."""
    resp = requests.get(
        url=f"{api}/{query}",
        headers=headers,
        timeout=30,
    )
    if resp.status_code == HTTPStatus.NO_CONTENT:
        return []
    return _original_send_rest_query(api, query, headers)


def _patched_list_files(self: GithubExtractor):
    """Skip file listing when the repo has no ``HEAD`` tree (empty GitHub repo)."""
    repo = self._repo_data
    obj = repo.get("object") if isinstance(repo, dict) else None
    if obj is None:
        logger.info(
            "GIMIE: repository has no HEAD tree (empty repo); skipping file parsers",
        )
        return []
    return _original_list_files(self)


gimie_github.send_rest_query = _patched_send_rest_query
GithubExtractor.list_files = _patched_list_files

from gimie.project import Project  # noqa: E402  # import after monkeypatches


def _first_non_empty_token(raw: str) -> str | None:
    return next((t.strip() for t in raw.split(",") if t.strip()), None)


@contextlib.contextmanager
def _gimie_legacy_github_token_env():
    """Force `GITHUB_TOKEN` to a single PAT for the duration of a gimie call.

    `gimie.extractors.github.GithubExtractor` reads `os.environ["GITHUB_TOKEN"]`
    verbatim and 401s when the value contains commas. The post-rename
    deployments expose the PAT pool under `GME_GITHUB_TOKEN_POOL` /
    `GME_GITHUB_TOKEN`, and many local shells still export the legacy
    `GITHUB_TOKEN` with the same comma-separated string — either way
    gimie needs to see one entry. Promote whichever source has a value
    (in order: POOL > GME single > legacy) to a single first token for
    the call, then restore.
    """
    prior = os.environ.get("GITHUB_TOKEN")
    raw = (
        os.environ.get("GME_GITHUB_TOKEN_POOL", "")
        or os.environ.get("GME_GITHUB_TOKEN", "")
        or os.environ.get("GITHUB_TOKEN", "")
    )
    first = _first_non_empty_token(raw)
    if first:
        os.environ["GITHUB_TOKEN"] = first
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = prior


def extract_gimie(full_path: str, serialization_format: str = "json-ld"):
    """
    Extracts the GIMIE project from the given URL.

    Args:
        full_path (str): The full path to the URL.
        serialization_format (str): Serialize graph as ``json-ld`` (default) or ``ttl``.

    Returns:
        Project: The GIMIE project object.
    """
    logger.info(f"Extracting GIMIE metadata for: {full_path}")

    with _gimie_legacy_github_token_env():
        proj = Project(full_path)
        g = proj.extract()

    if serialization_format == "json-ld":
        output = json.loads(g.serialize(format="json-ld"))
    else:
        output = g.serialize(format=serialization_format)

    if output is None:
        return None
    return output
