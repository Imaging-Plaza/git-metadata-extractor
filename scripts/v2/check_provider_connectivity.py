# ruff: noqa: INP001

from __future__ import annotations

import argparse
import json
import os
import sys
from importlib import import_module
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
DEFAULT_PROVIDERS = ("github", "ror", "orcid", "infoscience", "logfire", "selenium")
ENV_REQUIREMENTS_BY_PROVIDER = {
    "github": ("GITHUB_TOKEN",),
    "infoscience": ("INFOSCIENCE_TOKEN",),
    "selenium": ("SELENIUM_REMOTE_URL",),
}
MISSING_GITHUB_TOKEN_ERROR = "Missing required environment variable: GITHUB_TOKEN"  # noqa: S105
MISSING_LOGFIRE_TOKEN_ERROR = (
    "Missing Logfire credentials: set LOGFIRE_TOKEN or run `logfire projects use`."  # noqa: S105
)
LOGFIRE_TOKEN_SOURCE_WARNING = (
    "Both LOGFIRE_TOKEN and .logfire credentials are present; using .logfire token."  # noqa: S105
)
MISSING_SELENIUM_URL_ERROR = "Missing required environment variable: SELENIUM_REMOTE_URL"
GITHUB_UNAUTHORIZED_ERROR = (
    "GitHub token unauthorized (401). Check GITHUB_TOKEN value and scopes."
)
LOGFIRE_UNAUTHORIZED_ERROR = (
    "Logfire token unauthorized (401). Check project token and selected region."
)
LOGFIRE_UNREACHABLE_ERROR = "Logfire endpoint is unreachable."
LOGFIRE_INVALID_INFO_PAYLOAD_ERROR = "Logfire /v1/info returned invalid payload type."
LOGFIRE_INFO_STATUS_ERROR = "Logfire /v1/info returned unexpected status code"
LOGFIRE_BASE_URL_UNRESOLVED_ERROR = (
    "Could not resolve Logfire base URL. Set LOGFIRE_BASE_URL or run `logfire projects use`."
)
LOGFIRE_CREDENTIALS_FILE = "logfire_credentials.json"
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
HTTP_BAD_REQUEST = 400


def _load_logfire_credentials_payload() -> dict[str, str] | None:
    credentials_path = _resolve_logfire_credentials_path()
    if not credentials_path.exists():
        return None

    try:
        raw_payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw_payload, dict):
        return None

    normalized_payload: dict[str, str] = {}
    for key in ("token", "logfire_api_url"):
        value = raw_payload.get(key)
        if isinstance(value, str) and value.strip():
            normalized_payload[key] = value.strip()
    return normalized_payload or None


def _resolve_logfire_credentials_path() -> Path:
    credentials_dir = os.getenv("LOGFIRE_CREDENTIALS_DIR", ".logfire").strip() or ".logfire"
    return Path(credentials_dir) / LOGFIRE_CREDENTIALS_FILE


def _load_logfire_token_from_credentials() -> str | None:
    payload = _load_logfire_credentials_payload()
    if payload is None:
        return None
    return payload.get("token")


def _load_logfire_api_url_from_credentials() -> str | None:
    payload = _load_logfire_credentials_payload()
    if payload is None:
        return None
    return payload.get("logfire_api_url")


def _normalize_base_url(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    normalized = raw_value.strip().rstrip("/")
    return normalized or None


def _infer_logfire_base_url_from_token(token: str) -> str | None:
    try:
        logfire_config_module = import_module("logfire._internal.config")
    except ImportError:
        return None

    infer_base_url = getattr(logfire_config_module, "get_base_url_from_token", None)
    if not callable(infer_base_url):
        return None

    try:
        inferred = infer_base_url(token)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(inferred, str):
        return None
    return _normalize_base_url(inferred)


def _resolve_logfire_base_url(*, token: str, credentials_base_url: str | None) -> str:
    env_base_url = _normalize_base_url(os.getenv("LOGFIRE_BASE_URL"))
    if env_base_url:
        return env_base_url

    normalized_credentials_base_url = _normalize_base_url(credentials_base_url)
    if normalized_credentials_base_url:
        return normalized_credentials_base_url

    inferred_base_url = _infer_logfire_base_url_from_token(token)
    if inferred_base_url:
        return inferred_base_url
    raise RuntimeError(LOGFIRE_BASE_URL_UNRESOLVED_ERROR)


def get_missing_required_env_vars(providers: Sequence[str] | None = None) -> list[str]:
    selected_providers = (
        [provider.strip().lower() for provider in providers]
        if providers is not None
        else list(DEFAULT_PROVIDERS)
    )

    required_env_vars: set[str] = set()
    for provider in selected_providers:
        required_env_vars.update(ENV_REQUIREMENTS_BY_PROVIDER.get(provider, ()))

    if "logfire" in selected_providers:
        env_token = os.getenv("LOGFIRE_TOKEN", "").strip()
        credentials_token = _load_logfire_token_from_credentials()
        if not env_token and not credentials_token:
            required_env_vars.add("LOGFIRE_TOKEN")

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


def _check_logfire(timeout_seconds: int) -> None:
    env_token = os.getenv("LOGFIRE_TOKEN", "").strip() or None
    credentials_token = _load_logfire_token_from_credentials()
    credentials_base_url = _load_logfire_api_url_from_credentials()
    selected_token = credentials_token or env_token

    if not selected_token:
        raise RuntimeError(MISSING_LOGFIRE_TOKEN_ERROR)

    if env_token and credentials_token and env_token != credentials_token:
        print(f"[warn] logfire connectivity: {LOGFIRE_TOKEN_SOURCE_WARNING}")

    base_url = _resolve_logfire_base_url(
        token=selected_token,
        credentials_base_url=credentials_base_url,
    )
    info_url = f"{base_url}/v1/info"
    headers = {
        "Authorization": selected_token,
        "User-Agent": "git-metadata-extractor-preflight",
    }

    try:
        response = requests.get(
            info_url,
            headers=headers,
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        message = f"{LOGFIRE_UNREACHABLE_ERROR} {exc}"
        raise RuntimeError(message) from exc

    if response.status_code == HTTP_UNAUTHORIZED:
        raise RuntimeError(LOGFIRE_UNAUTHORIZED_ERROR)
    if response.status_code >= HTTP_BAD_REQUEST:
        message = f"{LOGFIRE_INFO_STATUS_ERROR}: {response.status_code}"
        raise RuntimeError(message)

    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError(LOGFIRE_INVALID_INFO_PAYLOAD_ERROR)


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
            elif provider == "logfire":
                _check_logfire(timeout_seconds)
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
