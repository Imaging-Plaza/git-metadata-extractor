# ruff: noqa: INP001

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=False)

from src.v2.ingest.providers.infoscience_provider import RealInfoscienceProvider  # noqa: E402
from src.v2.ingest.providers.orcid_provider import RealORCIDProvider  # noqa: E402
from src.v2.ingest.providers.ror_provider import RealRORProvider  # noqa: E402

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_PROVIDERS = ("github", "ror", "orcid", "infoscience", "selenium")
ENV_REQUIREMENTS_BY_PROVIDER = {
    "github": ("GITHUB_TOKEN",),
    "infoscience": ("INFOSCIENCE_TOKEN",),
    "selenium": ("SELENIUM_REMOTE_URL",),
}
MISSING_GITHUB_TOKEN_ERROR = "Missing required environment variable: GITHUB_TOKEN"  # noqa: S105
MISSING_SELENIUM_URL_ERROR = "Missing required environment variable: SELENIUM_REMOTE_URL"
GITHUB_UNAUTHORIZED_ERROR = (
    "GitHub token unauthorized (401). Check GITHUB_TOKEN value and scopes."
)
ROR_INVALID_PAYLOAD_ERROR = "ROR organization check returned an invalid payload"
ROR_EMPTY_SEARCH_ERROR = "ROR search check returned no results"
ORCID_INVALID_PAYLOAD_ERROR = "ORCID person check returned an invalid payload"
INFOSCIENCE_PEOPLE_TYPE_ERROR = "Infoscience person search returned invalid payload type"
INFOSCIENCE_ORGUNITS_TYPE_ERROR = (
    "Infoscience orgunit search returned invalid payload type"
)
INFOSCIENCE_PUBLICATIONS_TYPE_ERROR = (
    "Infoscience publication search returned invalid payload type"
)
SELENIUM_NOT_READY_ERROR = "Selenium service is reachable but not ready"
SELENIUM_INVALID_STATUS_ERROR = "Selenium /status returned invalid payload type"
HTTP_UNAUTHORIZED = 401


def get_missing_required_env_vars(providers: Sequence[str] | None = None) -> list[str]:
    selected_providers = (
        [provider.strip().lower() for provider in providers]
        if providers is not None
        else list(DEFAULT_PROVIDERS)
    )

    required_env_vars: set[str] = set()
    for provider in selected_providers:
        required_env_vars.update(ENV_REQUIREMENTS_BY_PROVIDER.get(provider, ()))

    missing_vars: list[str] = []
    for key in sorted(required_env_vars):
        value = os.getenv(key)
        if value is None or value.strip() == "":
            missing_vars.append(key)
    return missing_vars


def _check_github(timeout_seconds: int) -> None:
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if not github_token:
        raise RuntimeError(MISSING_GITHUB_TOKEN_ERROR)

    session = requests.Session()
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
    }

    checks = [
        (
            "repo",
            "https://api.github.com/repos/sdsc-ordes/gimie",
            dict,
        ),
        (
            "contributors",
            "https://api.github.com/repos/sdsc-ordes/gimie/contributors",
            list,
        ),
        (
            "languages",
            "https://api.github.com/repos/sdsc-ordes/gimie/languages",
            dict,
        ),
        (
            "organization",
            "https://api.github.com/orgs/sdsc-ordes",
            dict,
        ),
    ]

    for check_name, url, expected_type in checks:
        response = session.get(url, headers=headers, timeout=timeout_seconds)
        if response.status_code == HTTP_UNAUTHORIZED:
            raise RuntimeError(GITHUB_UNAUTHORIZED_ERROR)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, expected_type):
            message = (
                "GitHub connectivity check returned unexpected payload shape "
                f"for {check_name}"
            )
            raise TypeError(message)


def _check_ror() -> None:
    provider = RealRORProvider()
    organization = provider.get_organization("02s376052")
    if not isinstance(organization, dict) or not organization.get("id"):
        raise RuntimeError(ROR_INVALID_PAYLOAD_ERROR)

    matches = provider.search_organizations("EPFL")
    if not isinstance(matches, list) or not matches:
        raise RuntimeError(ROR_EMPTY_SEARCH_ERROR)


def _check_orcid() -> None:
    provider = RealORCIDProvider()
    record = provider.get_person_by_orcid("0000-0002-1825-0097")
    if not isinstance(record, dict) or not record.get("orcid_id"):
        raise RuntimeError(ORCID_INVALID_PAYLOAD_ERROR)


def _check_infoscience() -> None:
    provider = RealInfoscienceProvider(max_results=5)
    people = provider.search_person("alice smith")
    orgunits = provider.search_orgunit("EPFL")
    publications = provider.search_publications("metadata")

    if not isinstance(people, list):
        raise TypeError(INFOSCIENCE_PEOPLE_TYPE_ERROR)
    if not isinstance(orgunits, list):
        raise TypeError(INFOSCIENCE_ORGUNITS_TYPE_ERROR)
    if not isinstance(publications, list):
        raise TypeError(INFOSCIENCE_PUBLICATIONS_TYPE_ERROR)


def _check_selenium(timeout_seconds: int) -> None:
    selenium_remote_url = os.getenv("SELENIUM_REMOTE_URL", "").strip()
    if not selenium_remote_url:
        raise RuntimeError(MISSING_SELENIUM_URL_ERROR)

    status_url = f"{selenium_remote_url.rstrip('/')}/status"
    response = requests.get(status_url, timeout=timeout_seconds)
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError(SELENIUM_INVALID_STATUS_ERROR)

    value = payload.get("value")
    if isinstance(value, dict) and value.get("ready") is False:
        raise RuntimeError(SELENIUM_NOT_READY_ERROR)


def run_provider_connectivity_checks(
    providers: Sequence[str],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[str]:
    failures: list[str] = []

    normalized_providers = [provider.strip().lower() for provider in providers]
    for provider in normalized_providers:
        try:
            if provider == "github":
                _check_github(timeout_seconds)
            elif provider == "ror":
                _check_ror()
            elif provider == "orcid":
                _check_orcid()
            elif provider == "infoscience":
                _check_infoscience()
            elif provider == "selenium":
                _check_selenium(timeout_seconds)
            else:
                failures.append(f"Unknown provider: {provider}")
                continue
            print(f"[ok] {provider} connectivity")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{provider}: {exc}")

    return failures


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live connectivity checks for v2 providers")
    parser.add_argument(
        "--providers",
        nargs="+",
        default=list(DEFAULT_PROVIDERS),
        choices=list(DEFAULT_PROVIDERS),
        help="Providers to validate. Defaults to all providers.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="HTTP request timeout for connectivity checks.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    missing_env_vars = get_missing_required_env_vars(args.providers)
    if missing_env_vars:
        missing_message = ", ".join(sorted(missing_env_vars))
        print(
            f"Missing required environment variable(s): {missing_message}",
            file=sys.stderr,
        )
        return 2

    failures = run_provider_connectivity_checks(
        args.providers,
        timeout_seconds=args.timeout_seconds,
    )
    if failures:
        print("Provider connectivity failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("All requested provider connectivity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
