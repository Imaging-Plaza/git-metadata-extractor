"""Tests for the github_users + github_organizations auto-ingest hooks
on `/v2/extract`.

These exercise the URL-extraction helper + the env-flag gating only.
The full end-to-end ingest path is exercised by the per-module ingest
tests; here we only assert that:
  - the URL parser correctly extracts a bare login from user/org URLs
  - the helpers are off by default (no flag → no-op)
  - the right helper fires for each detected_type
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from git_metadata_extractor.api.auto_ingest import (
    _github_account_login_from_url,
    _maybe_schedule_github_orgs_auto_ingest,
    _maybe_schedule_github_users_auto_ingest,
)


# ---------------------------------------------------------------------------
# URL → bare login helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/octocat", "octocat"),
        ("http://github.com/caviri", "caviri"),
        ("https://github.com/octocat/", "octocat"),
        # Web-UI org URL form: github.com/orgs/<login>
        ("https://github.com/orgs/EPFL-ENAC", "EPFL-ENAC"),
        ("https://github.com/orgs/EPFL-ENAC/", "EPFL-ENAC"),
    ],
)
def test_github_account_login_from_url_accepts_user_and_org_shapes(
    url: str, expected: str,
) -> None:
    assert _github_account_login_from_url(url) == expected


@pytest.mark.parametrize(
    "bad_url",
    [
        None,
        "",
        "https://example.com/octocat",            # wrong host
        "https://github.com/octocat/Hello-World", # repo URL — has a slash
        "https://github.com/",                    # empty login
        "octocat",                                # not a URL
        42,                                       # wrong type
    ],
)
def test_github_account_login_from_url_rejects_bad_inputs(bad_url) -> None:
    assert _github_account_login_from_url(bad_url) is None


# ---------------------------------------------------------------------------
# Env-flag gating
# ---------------------------------------------------------------------------


def _classification(detected_type: str, normalized_url: str) -> object:
    return SimpleNamespace(
        detected_type=SimpleNamespace(value=detected_type),
        normalized_url=normalized_url,
    )


def test_github_users_auto_ingest_is_off_by_default() -> None:
    """No env flag => the scheduler must return without trying to run."""
    with patch("asyncio.create_task") as mock_create:
        _maybe_schedule_github_users_auto_ingest(
            classification=_classification("user", "https://github.com/octocat"),
            run_id="run-1",
        )
    mock_create.assert_not_called()


def test_github_users_auto_ingest_ignores_repository_detection() -> None:
    """Flag on, but detected_type=repository → wrong helper, no schedule."""
    with patch.dict(os.environ, {"V2_GITHUB_USERS_RAG_AUTO_INGEST": "true"}), patch(
        "asyncio.create_task",
    ) as mock_create:
        _maybe_schedule_github_users_auto_ingest(
            classification=_classification(
                "repository", "https://github.com/octocat/Hello-World",
            ),
            run_id="run-1",
        )
    mock_create.assert_not_called()


def test_github_users_auto_ingest_ignores_organization_detection() -> None:
    """Flag on, detected_type=organization → org helper handles it, not this one."""
    with patch.dict(os.environ, {"V2_GITHUB_USERS_RAG_AUTO_INGEST": "true"}), patch(
        "asyncio.create_task",
    ) as mock_create:
        _maybe_schedule_github_users_auto_ingest(
            classification=_classification(
                "organization", "https://github.com/orgs/EPFL-ENAC",
            ),
            run_id="run-1",
        )
    mock_create.assert_not_called()


def test_github_users_auto_ingest_schedules_for_user_when_enabled() -> None:
    """Flag on + detected_type=user + valid URL → background task is scheduled."""
    with patch.dict(os.environ, {"V2_GITHUB_USERS_RAG_AUTO_INGEST": "true"}), patch(
        "asyncio.create_task",
    ) as mock_create:
        _maybe_schedule_github_users_auto_ingest(
            classification=_classification("user", "https://github.com/octocat"),
            run_id="run-1",
        )
    assert mock_create.call_count == 1


def test_github_orgs_auto_ingest_is_off_by_default() -> None:
    with patch("asyncio.create_task") as mock_create:
        _maybe_schedule_github_orgs_auto_ingest(
            classification=_classification(
                "organization", "https://github.com/orgs/EPFL-ENAC",
            ),
            run_id="run-1",
        )
    mock_create.assert_not_called()


def test_github_orgs_auto_ingest_ignores_user_detection() -> None:
    with patch.dict(os.environ, {"V2_GITHUB_ORGS_RAG_AUTO_INGEST": "true"}), patch(
        "asyncio.create_task",
    ) as mock_create:
        _maybe_schedule_github_orgs_auto_ingest(
            classification=_classification("user", "https://github.com/octocat"),
            run_id="run-1",
        )
    mock_create.assert_not_called()


def test_github_orgs_auto_ingest_schedules_for_organization_when_enabled() -> None:
    with patch.dict(os.environ, {"V2_GITHUB_ORGS_RAG_AUTO_INGEST": "true"}), patch(
        "asyncio.create_task",
    ) as mock_create:
        _maybe_schedule_github_orgs_auto_ingest(
            classification=_classification(
                "organization", "https://github.com/orgs/EPFL-ENAC",
            ),
            run_id="run-1",
        )
    assert mock_create.call_count == 1


def test_github_orgs_auto_ingest_accepts_plain_org_url() -> None:
    """The classifier may emit either `/orgs/<login>` (web UI) or
    `/<login>` (canonical). Both should resolve to the bare handle."""
    with patch.dict(os.environ, {"V2_GITHUB_ORGS_RAG_AUTO_INGEST": "true"}), patch(
        "asyncio.create_task",
    ) as mock_create:
        _maybe_schedule_github_orgs_auto_ingest(
            classification=_classification(
                "organization", "https://github.com/EPFL-ENAC",
            ),
            run_id="run-1",
        )
    assert mock_create.call_count == 1
