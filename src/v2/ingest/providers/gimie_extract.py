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
from functools import cache
from http import HTTPStatus
from typing import Any

import requests

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


# gimie is imported LAZILY (below) so this module loads even when the `gimie`
# package isn't installed — the in-process path is only one of two backends
# (the other is the gimie-api sidecar, selected by GIMIE_API_URL). Our gimie
# monkeypatches (CFF-date normalisation + GitHub 204/empty-repo resilience) are
# applied once on first in-process use (the @cache on `_ensure_gimie_project`
# guarantees once-only).


def _apply_gimie_patches() -> None:
    """Apply our CFF-date + GitHub-resilience monkeypatches to gimie."""
    import gimie.extractors.github as gimie_github  # noqa: PLC0415
    from gimie.extractors.github import GithubExtractor  # noqa: PLC0415
    from gimie.parsers.cff import CffParser  # noqa: PLC0415

    _original_cff_parse = CffParser.parse

    def _patched_cff_parse(self: Any, data: bytes) -> Any:
        return _original_cff_parse(self, _normalize_cff_calendar_dates(data))

    CffParser.parse = _patched_cff_parse  # type: ignore[method-assign]

    _original_send_rest_query = gimie_github.send_rest_query

    def _patched_send_rest_query(
        api: str, query: str, headers: dict[str, str],
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Treat GitHub 204 (empty body) as an empty JSON list (no contributors)."""
        resp = requests.get(url=f"{api}/{query}", headers=headers, timeout=30)
        if resp.status_code == HTTPStatus.NO_CONTENT:
            return []
        return _original_send_rest_query(api, query, headers)

    _original_list_files = GithubExtractor.list_files

    def _patched_list_files(self: Any) -> Any:
        """Skip file listing when the repo has no ``HEAD`` tree (empty repo)."""
        repo = self._repo_data
        obj = repo.get("object") if isinstance(repo, dict) else None
        if obj is None:
            logger.info(
                "GIMIE: repository has no HEAD tree (empty repo); skipping file parsers",
            )
            return []
        return _original_list_files(self)

    gimie_github.send_rest_query = _patched_send_rest_query
    GithubExtractor.list_files = _patched_list_files  # type: ignore[method-assign]


@cache
def _ensure_gimie_project() -> Any:
    """Lazily import gimie (applying our patches once) and return ``Project``.

    Raises ``RuntimeError`` with actionable guidance when ``gimie`` isn't
    installed — the deployment is expected to set ``GIMIE_API_URL`` (sidecar)
    instead of running gimie in-process. ``@cache`` makes the import + patches
    run exactly once.
    """
    try:
        _apply_gimie_patches()
        from gimie.project import Project  # noqa: PLC0415
    except ImportError as exc:
        message = (
            "in-process gimie extraction requires the `gimie` package, which is "
            "not installed. Set GIMIE_API_URL to use the gimie-api sidecar, or "
            "`pip install gimie==0.7.2` for in-process extraction."
        )
        raise RuntimeError(message) from exc
    return Project


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
    """Extract a repo's GIMIE metadata — via the gimie-api sidecar when
    ``GIMIE_API_URL`` is set, otherwise in-process gimie.

    This is the single **intermediate** every caller (v1 + v2) goes through, so
    the in-process gimie dependency can be swapped for the sidecar in one place.
    When the sidecar is configured it is authoritative (returns ``None`` on its
    own failure, matching the in-process degrade path); there is no silent
    in-process fallback (the sidecar exists precisely so gimie can leave the
    Python tree).

    Args:
        full_path (str): The repository URL.
        serialization_format (str): ``json-ld`` (default) or ``ttl``.
    """
    # Prefer the sidecar when configured (no gimie import needed on this path).
    from src.v2.ingest.providers.gimie_api_client import (  # noqa: PLC0415
        extract_gimie_via_api,
        gimie_api_base,
    )

    if gimie_api_base() is not None:
        return extract_gimie_via_api(full_path, serialization_format)

    logger.info("Extracting GIMIE metadata (in-process) for: %s", full_path)
    project_cls = _ensure_gimie_project()
    with _gimie_legacy_github_token_env():
        proj = project_cls(full_path)
        g = proj.extract()

    if serialization_format == "json-ld":
        output = json.loads(g.serialize(format="json-ld"))
    else:
        output = g.serialize(format=serialization_format)

    if output is None:
        return None
    return output
