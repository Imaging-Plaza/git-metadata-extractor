# ruff: noqa: INP001

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=False)

from src.v2.testing.provider_snapshot_sanitizer import (  # noqa: E402
    sanitize_json_payload,
    sanitize_snapshot_metadata,
)

DEFAULT_DATASET = "gimie-baseline"
DEFAULT_PROVIDERS = ("github", "ror", "orcid", "infoscience")
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / ".internal" / "phase-8" / "captures"
DEFAULT_PROMOTE_ROOT = (
    PROJECT_ROOT / "tests" / "v2" / "fixtures" / "providers" / "live_snapshots"
)
MISSING_GITHUB_TOKEN_ERROR = "Missing required environment variable: GITHUB_TOKEN"  # noqa: S105
MISSING_INFOSCIENCE_TOKEN_ERROR = (
    "Missing required environment variable: INFOSCIENCE_TOKEN"  # noqa: S105
)
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403


@dataclass(frozen=True)
class EndpointSpec:
    provider: str
    case_name: str
    method: str
    url: str
    params: Mapping[str, str] | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _capture_timestamp(default_time: datetime | None = None) -> str:
    effective_time = default_time or _utc_now()
    return effective_time.strftime("%Y%m%dT%H%M%SZ")


def _required_env_for_providers(providers: Sequence[str]) -> list[str]:
    requirements: dict[str, tuple[str, ...]] = {
        "github": ("GITHUB_TOKEN",),
        "infoscience": ("INFOSCIENCE_TOKEN",),
    }
    missing: list[str] = []

    for provider in providers:
        for env_name in requirements.get(provider, ()):
            value = os.getenv(env_name)
            if value is None or value.strip() == "":
                missing.append(env_name)

    return sorted(set(missing))


def _dataset_specs(dataset: str) -> list[EndpointSpec]:
    if dataset != DEFAULT_DATASET:
        message = f"Unsupported dataset: {dataset}"
        raise ValueError(message)

    return [
        EndpointSpec(
            provider="github",
            case_name="repo_sdsc-ordes_gimie",
            method="GET",
            url="https://api.github.com/repos/sdsc-ordes/gimie",
        ),
        EndpointSpec(
            provider="github",
            case_name="contributors_sdsc-ordes_gimie",
            method="GET",
            url="https://api.github.com/repos/sdsc-ordes/gimie/contributors",
        ),
        EndpointSpec(
            provider="github",
            case_name="languages_sdsc-ordes_gimie",
            method="GET",
            url="https://api.github.com/repos/sdsc-ordes/gimie/languages",
        ),
        EndpointSpec(
            provider="github",
            case_name="org_sdsc-ordes",
            method="GET",
            url="https://api.github.com/orgs/sdsc-ordes",
        ),
        EndpointSpec(
            provider="ror",
            case_name="org_02s376052",
            method="GET",
            url="https://api.ror.org/v2/organizations/02s376052",
        ),
        EndpointSpec(
            provider="ror",
            case_name="search_epfl",
            method="GET",
            url="https://api.ror.org/v2/organizations",
            params={"query": "EPFL"},
        ),
        EndpointSpec(
            provider="orcid",
            case_name="person_0000-0002-1825-0097",
            method="GET",
            url="https://pub.orcid.org/v3.0/0000-0002-1825-0097/person",
        ),
        EndpointSpec(
            provider="orcid",
            case_name="employments_0000-0002-1825-0097",
            method="GET",
            url="https://pub.orcid.org/v3.0/0000-0002-1825-0097/employments",
        ),
        EndpointSpec(
            provider="orcid",
            case_name="educations_0000-0002-1825-0097",
            method="GET",
            url="https://pub.orcid.org/v3.0/0000-0002-1825-0097/educations",
        ),
        EndpointSpec(
            provider="infoscience",
            case_name="search_person_alice_smith",
            method="GET",
            url="https://infoscience.epfl.ch/server/api/discover/search/objects",
            params={"query": "alice smith", "size": "10", "configuration": "person"},
        ),
        EndpointSpec(
            provider="infoscience",
            case_name="search_orgunit_epfl",
            method="GET",
            url="https://infoscience.epfl.ch/server/api/discover/search/objects",
            params={"query": "EPFL", "size": "10", "configuration": "orgunit"},
        ),
        EndpointSpec(
            provider="infoscience",
            case_name="search_publications_metadata",
            method="GET",
            url="https://infoscience.epfl.ch/server/api/discover/search/objects",
            params={
                "query": "metadata",
                "size": "10",
                "configuration": "researchoutputs",
            },
        ),
    ]


def _provider_headers(provider: str, *, include_auth: bool = True) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "git-metadata-extractor-phase8/1.0",
    }

    if provider == "github":
        github_token = os.getenv("GITHUB_TOKEN", "").strip()
        if not github_token:
            raise RuntimeError(MISSING_GITHUB_TOKEN_ERROR)
        headers["Authorization"] = f"Bearer {github_token}"

    if provider == "infoscience" and include_auth:
        infoscience_token = os.getenv("INFOSCIENCE_TOKEN", "").strip()
        if not infoscience_token:
            raise RuntimeError(MISSING_INFOSCIENCE_TOKEN_ERROR)
        headers["Authorization"] = f"Bearer {infoscience_token}"

    if provider == "orcid":
        headers["Accept"] = "application/json"

    return headers


def _response_payload(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {
            "raw_text": response.text,
            "content_type": response.headers.get("Content-Type"),
        }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _capture_one(
    *,
    session: requests.Session,
    spec: EndpointSpec,
    output_root: Path,
    timeout_seconds: int,
) -> tuple[Path, Path, bool, int]:
    request_headers = _provider_headers(spec.provider)
    captured_at = _iso_utc(_utc_now())
    params = dict(spec.params) if spec.params else None
    response = session.request(
        method=spec.method,
        url=spec.url,
        params=params,
        headers=request_headers,
        timeout=timeout_seconds,
    )

    auth_fallback_used = False
    initial_status_code: int | None = None
    should_retry_without_auth = (
        spec.provider == "infoscience"
        and response.status_code in {HTTP_UNAUTHORIZED, HTTP_FORBIDDEN}
    )
    if should_retry_without_auth:
        auth_fallback_used = True
        initial_status_code = response.status_code
        request_headers = _provider_headers(spec.provider, include_auth=False)
        response = session.request(
            method=spec.method,
            url=spec.url,
            params=params,
            headers=request_headers,
            timeout=timeout_seconds,
        )

    response_body = sanitize_json_payload(_response_payload(response))
    metadata = {
        "captured_at": captured_at,
        "provider": spec.provider,
        "case": spec.case_name,
        "request": {
            "method": spec.method,
            "url": spec.url,
            "query": dict(spec.params) if spec.params else {},
            "headers": request_headers,
            "auth_fallback_used": auth_fallback_used,
        },
        "response": {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "ok": response.ok,
            "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
        },
    }
    if initial_status_code is not None:
        metadata["response"]["initial_status_code"] = initial_status_code
    metadata = sanitize_snapshot_metadata(metadata)

    provider_root = output_root / spec.provider
    response_path = provider_root / f"{spec.case_name}.response.json"
    meta_path = provider_root / f"{spec.case_name}.meta.json"

    _write_json(response_path, response_body)
    _write_json(meta_path, metadata)

    return response_path, meta_path, response.ok, response.status_code


def _capture_with_result(
    *,
    session: requests.Session,
    spec: EndpointSpec,
    output_root: Path,
    timeout_seconds: int,
) -> tuple[bool, Path | None, Path | None, bool, int | None, str | None]:
    response_path: Path | None = None
    meta_path: Path | None = None
    ok = False
    status_code: int | None = None
    try:
        response_path, meta_path, ok, status_code = _capture_one(
            session=session,
            spec=spec,
            output_root=output_root,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        return False, None, None, False, None, str(exc)
    return True, response_path, meta_path, ok, status_code, None


def _selected_specs(dataset: str, providers: Sequence[str]) -> list[EndpointSpec]:
    provider_set = {provider.strip().lower() for provider in providers}
    return [
        spec
        for spec in _dataset_specs(dataset)
        if spec.provider in provider_set
    ]


def _collect_manifest_entries(promote_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for meta_path in sorted(promote_root.glob("**/*.meta.json")):
        if meta_path.name == "manifest.meta.json":
            continue

        with meta_path.open(encoding="utf-8") as handle:
            meta_payload = json.load(handle)

        if not isinstance(meta_payload, dict):
            continue

        provider = str(meta_payload.get("provider", "")).strip()
        case_name = str(meta_payload.get("case", "")).strip()
        response_info = meta_payload.get("response", {})
        status_code = None
        if isinstance(response_info, dict):
            status_code = response_info.get("status_code")

        response_path = meta_path.with_suffix("").with_suffix(".response.json")
        if not response_path.exists():
            response_path = meta_path.parent / f"{case_name}.response.json"

        entries.append(
            {
                "provider": provider,
                "case": case_name,
                "captured_at": meta_payload.get("captured_at"),
                "status_code": status_code,
                "response_path": str(response_path.relative_to(promote_root)),
                "meta_path": str(meta_path.relative_to(promote_root)),
            },
        )

    entries.sort(key=lambda item: (item.get("provider", ""), item.get("case", "")))
    return entries


def _write_manifest(promote_root: Path, *, dataset: str) -> None:
    manifest = {
        "version": 1,
        "dataset": dataset,
        "updated_at": _iso_utc(_utc_now()),
        "entries": _collect_manifest_entries(promote_root),
    }
    _write_json(promote_root / "manifest.json", manifest)


def _promote_captures(capture_root: Path, promote_root: Path, *, dataset: str) -> int:
    copied_files = 0
    for path in sorted(capture_root.glob("**/*.json")):
        if not path.is_file():
            continue

        relative_path = path.relative_to(capture_root)
        target_path = promote_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target_path)
        copied_files += 1

    _write_manifest(promote_root, dataset=dataset)
    return copied_files


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture provider snapshots for Phase 8 and optionally promote "
            "sanitized artifacts to test fixtures"
        ),
    )
    parser.add_argument(
        "--providers",
        nargs="+",
        default=list(DEFAULT_PROVIDERS),
        choices=list(DEFAULT_PROVIDERS),
        help="Providers to capture from.",
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        choices=[DEFAULT_DATASET],
        help="Named dataset to capture.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Capture staging root directory.",
    )
    parser.add_argument(
        "--timestamp",
        type=str,
        default=None,
        help="Optional fixed timestamp suffix (format: YYYYMMDDTHHMMSSZ).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--promote-to",
        type=Path,
        default=None,
        help=(
            "Promote sanitized capture JSON files into a fixture namespace, "
            f"for example: {DEFAULT_PROMOTE_ROOT}"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    providers = [provider.strip().lower() for provider in args.providers]
    missing_env_vars = _required_env_for_providers(providers)
    if missing_env_vars:
        missing = ", ".join(missing_env_vars)
        print(f"Missing required environment variable(s): {missing}", file=sys.stderr)
        return 2

    capture_timestamp = args.timestamp or _capture_timestamp()
    capture_root = args.output_root / capture_timestamp
    capture_root.mkdir(parents=True, exist_ok=True)

    specs = _selected_specs(args.dataset, providers)
    failures = 0

    with requests.Session() as session:
        for spec in specs:
            success, response_path, meta_path, ok, status_code, error_message = _capture_with_result(
                session=session,
                spec=spec,
                output_root=capture_root,
                timeout_seconds=args.timeout_seconds,
            )
            if success and response_path is not None:
                status = "ok" if ok else "error"
                status_details = (
                    f" (status={status_code})" if status_code is not None else ""
                )
                print(
                    f"[{status}] {spec.provider}/{spec.case_name}{status_details} -> {response_path}",
                )
                if not ok:
                    failures += 1
                    if meta_path is not None:
                        print(f"  metadata: {meta_path}")
            else:
                failures += 1
                print(
                    f"[error] {spec.provider}/{spec.case_name}: {error_message}",
                    file=sys.stderr,
                )

    if args.promote_to is not None:
        copied_files = _promote_captures(
            capture_root,
            args.promote_to,
            dataset=args.dataset,
        )
        print(
            f"Promoted {copied_files} json files to {args.promote_to}",
        )

    if failures:
        print(f"Snapshot capture completed with {failures} failure(s).", file=sys.stderr)
        return 1

    print(f"Snapshot capture completed successfully in {capture_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
