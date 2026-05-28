from __future__ import annotations

import pytest

from scripts.v2.check_provider_connectivity import (
    DEFAULT_PROVIDERS,
    get_missing_required_env_vars,
    run_provider_connectivity_checks,
)

pytestmark = [pytest.mark.live_provider]


@pytest.fixture(autouse=True)
def _require_live_marker_selection(
    pytestconfig: pytest.Config,
) -> None:
    mark_expression = pytestconfig.getoption("-m") or ""
    if "live_provider" not in mark_expression:
        pytest.skip(
            "live_provider tests run only when explicitly selected with -m live_provider",
        )


def test_live_provider_connectivity_smoke() -> None:
    missing_vars = get_missing_required_env_vars()
    if missing_vars:
        missing = ", ".join(sorted(missing_vars))
        pytest.skip(
            f"Missing required environment variable(s) for live connectivity: {missing}",
        )

    failures = run_provider_connectivity_checks(DEFAULT_PROVIDERS)
    assert failures == []
