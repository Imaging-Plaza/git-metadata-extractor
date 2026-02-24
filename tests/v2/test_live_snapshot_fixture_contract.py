from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.v2.testing.provider_snapshot_sanitizer import contains_secret_like_text

LIVE_SNAPSHOT_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "providers"
    / "live_snapshots"
)
MANIFEST_PATH = LIVE_SNAPSHOT_ROOT / "manifest.json"
EXPECTED_PROVIDERS = {"github", "ror", "orcid", "infoscience"}
REQUIRED_ENTRY_FIELDS = {
    "provider",
    "case",
    "captured_at",
    "status_code",
    "response_path",
    "meta_path",
}
REQUIRED_META_FIELDS = {"captured_at", "provider", "case", "request", "response"}


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _walk_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str):
                strings.append(key)
            strings.extend(_walk_strings(nested))
        return strings

    if isinstance(value, list):
        for nested in value:
            strings.extend(_walk_strings(nested))
        return strings

    if isinstance(value, str):
        return [value]

    return strings


def test_live_snapshot_manifest_has_expected_shape() -> None:
    manifest = _load_json(MANIFEST_PATH)

    assert isinstance(manifest, dict)
    assert manifest.get("version") == 1
    assert manifest.get("dataset") == "gimie-baseline"

    entries = manifest.get("entries")
    assert isinstance(entries, list)
    assert entries

    providers = {entry.get("provider") for entry in entries if isinstance(entry, dict)}
    assert providers == EXPECTED_PROVIDERS

    for entry in entries:
        assert isinstance(entry, dict)
        assert REQUIRED_ENTRY_FIELDS.issubset(entry)


def test_manifest_entries_reference_existing_snapshot_files() -> None:
    manifest = _load_json(MANIFEST_PATH)
    entries = manifest.get("entries", [])

    for entry in entries:
        response_path = LIVE_SNAPSHOT_ROOT / str(entry["response_path"])
        meta_path = LIVE_SNAPSHOT_ROOT / str(entry["meta_path"])

        assert response_path.exists()
        assert meta_path.exists()


def test_live_snapshot_meta_files_have_required_fields() -> None:
    manifest = _load_json(MANIFEST_PATH)
    entries = manifest.get("entries", [])

    for entry in entries:
        meta_path = LIVE_SNAPSHOT_ROOT / str(entry["meta_path"])
        payload = _load_json(meta_path)

        assert isinstance(payload, dict)
        assert REQUIRED_META_FIELDS.issubset(payload)

        request = payload["request"]
        response = payload["response"]
        assert isinstance(request, dict)
        assert isinstance(response, dict)
        assert isinstance(response.get("status_code"), int)


def test_live_snapshot_payload_files_are_non_empty_json_documents() -> None:
    manifest = _load_json(MANIFEST_PATH)
    entries = manifest.get("entries", [])

    for entry in entries:
        response_path = LIVE_SNAPSHOT_ROOT / str(entry["response_path"])
        payload = _load_json(response_path)

        assert isinstance(payload, (dict, list))
        if isinstance(payload, dict):
            assert payload


def test_live_snapshot_files_do_not_contain_secret_like_text() -> None:
    for json_path in sorted(LIVE_SNAPSHOT_ROOT.glob("**/*.json")):
        payload = _load_json(json_path)
        for text in _walk_strings(payload):
            assert not contains_secret_like_text(text), (
                f"Secret-like value found in {json_path}: {text}"
            )
