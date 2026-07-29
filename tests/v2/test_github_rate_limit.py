from __future__ import annotations

from datetime import datetime, timezone

from git_metadata_extractor.observation.github_rate_limit import (
    GitHubTokenStatus,
    _summarize,
)


def _ts(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_summarize_empty_pool_is_unhealthy() -> None:
    summary = _summarize([])
    assert summary.status == "unhealthy"
    assert summary.total_remaining == 0
    assert summary.earliest_reset is None


def test_summarize_all_invalid_is_unhealthy() -> None:
    summary = _summarize(
        [
            GitHubTokenStatus(index=1, status="invalid"),
            GitHubTokenStatus(index=2, status="invalid"),
        ],
    )
    assert summary.status == "unhealthy"


def test_summarize_all_rate_limited_is_degraded() -> None:
    summary = _summarize(
        [
            GitHubTokenStatus(
                index=1,
                status="rate_limited",
                core_remaining=0,
                core_limit=5000,
                core_reset=_ts(1_700_000_000),
            ),
            GitHubTokenStatus(
                index=2,
                status="rate_limited",
                core_remaining=0,
                core_limit=5000,
                core_reset=_ts(1_700_000_900),
            ),
        ],
    )
    assert summary.status == "degraded"
    assert summary.total_remaining == 0
    assert summary.earliest_reset == _ts(1_700_000_000)


def test_summarize_low_total_remaining_is_degraded() -> None:
    summary = _summarize(
        [
            GitHubTokenStatus(
                index=1,
                status="ok",
                core_remaining=100,
                core_limit=5000,
                core_reset=_ts(1_700_000_000),
            ),
        ],
    )
    assert summary.status == "degraded"
    assert summary.total_remaining == 100


def test_summarize_one_ok_one_rate_limited_is_healthy() -> None:
    summary = _summarize(
        [
            GitHubTokenStatus(
                index=1,
                status="rate_limited",
                core_remaining=0,
                core_limit=5000,
                core_reset=_ts(1_700_000_000),
            ),
            GitHubTokenStatus(
                index=2,
                status="ok",
                core_remaining=4500,
                core_limit=5000,
                core_reset=_ts(1_700_000_900),
            ),
        ],
    )
    assert summary.status == "healthy"
    assert summary.total_remaining == 4500
    assert summary.earliest_reset == _ts(1_700_000_000)


def test_summarize_unreachable_token_does_not_block_healthy() -> None:
    summary = _summarize(
        [
            GitHubTokenStatus(index=1, status="unreachable"),
            GitHubTokenStatus(
                index=2,
                status="ok",
                core_remaining=4800,
                core_limit=5000,
                core_reset=_ts(1_700_000_000),
            ),
        ],
    )
    assert summary.status == "healthy"
    assert summary.total_remaining == 4800
