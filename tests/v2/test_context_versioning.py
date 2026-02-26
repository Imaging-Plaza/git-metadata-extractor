from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.v2.graph.export import JSONLDExporter
from src.v2.validation.ontology import ontology_ttl_path

PREFIX_PATTERN = re.compile(r"^@prefix\s+([A-Za-z][A-Za-z0-9_-]*):\s+<[^>]+>\s+\.$")


def _context_file_path() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "v2" / "schemas" / "context" / "v2.0.jsonld"


def _ttl_prefixes(ttl_path: Path) -> set[str]:
    prefixes: set[str] = set()
    for line in ttl_path.read_text(encoding="utf-8").splitlines():
        match = PREFIX_PATTERN.match(line.strip())
        if match is not None:
            prefixes.add(match.group(1))
    return prefixes


def _assert_context_covers_ttl_prefixes(
    *,
    ttl_path: Path,
    context_payload: dict[str, object],
) -> None:
    raw_context = context_payload.get("@context")
    assert isinstance(raw_context, dict)
    missing = _ttl_prefixes(ttl_path) - set(raw_context.keys())
    assert not missing, f"Missing prefixes in context: {sorted(missing)}"


def _assert_term_mapping(
    context_payload: dict[str, object],
    *,
    term: str,
    expected: dict[str, str],
) -> None:
    raw_context = context_payload.get("@context")
    assert isinstance(raw_context, dict)
    mapping = raw_context.get(term)
    assert isinstance(mapping, dict), f"Missing object mapping for {term}"
    for key, value in expected.items():
        assert mapping.get(key) == value, f"{term} mapping {key} mismatch"


def test_context_file_exists_and_is_valid_jsonld() -> None:
    context_path = _context_file_path()

    assert context_path.exists()
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert isinstance(payload.get("@context"), dict)


def test_context_file_contains_all_ttl_namespace_prefixes() -> None:
    payload = json.loads(_context_file_path().read_text(encoding="utf-8"))

    _assert_context_covers_ttl_prefixes(
        ttl_path=ontology_ttl_path(),
        context_payload=payload,
    )


def test_context_file_contains_version_identifier() -> None:
    payload = json.loads(_context_file_path().read_text(encoding="utf-8"))

    assert isinstance(payload.get("context_version"), str)
    assert payload["context_version"]


def test_exporter_returns_context_from_file() -> None:
    exporter = JSONLDExporter()
    file_payload = json.loads(_context_file_path().read_text(encoding="utf-8"))

    assert exporter.get_context() == file_payload


def test_prefix_drift_detection_fails_when_ttl_adds_unmapped_prefix(tmp_path) -> None:
    original_ttl = ontology_ttl_path().read_text(encoding="utf-8")
    ttl_with_new_prefix = tmp_path / "ontology-with-extra-prefix.ttl"
    ttl_with_new_prefix.write_text(
        f"{original_ttl}\n@prefix ex: <http://example.org/ns#> .\n",
        encoding="utf-8",
    )
    payload = json.loads(_context_file_path().read_text(encoding="utf-8"))

    with pytest.raises(AssertionError, match="ex"):
        _assert_context_covers_ttl_prefixes(
            ttl_path=ttl_with_new_prefix,
            context_payload=payload,
        )


def test_context_file_promotes_relationship_term_mappings() -> None:
    payload = json.loads(_context_file_path().read_text(encoding="utf-8"))

    _assert_term_mapping(
        payload,
        term="schema:author",
        expected={"@type": "@id", "@container": "@set"},
    )
    _assert_term_mapping(
        payload,
        term="pulse:contributionTo",
        expected={"@type": "@id"},
    )
    _assert_term_mapping(
        payload,
        term="org:hasMembership",
        expected={"@type": "@id", "@container": "@set"},
    )
    _assert_term_mapping(
        payload,
        term="pulse:repositoryType",
        expected={"@type": "@id"},
    )
    _assert_term_mapping(
        payload,
        term="schema:url",
        expected={"@type": "@id"},
    )


def test_context_file_promotes_datatype_term_mappings() -> None:
    payload = json.loads(_context_file_path().read_text(encoding="utf-8"))

    _assert_term_mapping(
        payload,
        term="schema:dateCreated",
        expected={"@type": "xsd:dateTime"},
    )
    _assert_term_mapping(
        payload,
        term="schema:datePublished",
        expected={"@type": "xsd:date"},
    )
    _assert_term_mapping(
        payload,
        term="pulse:contributionCount",
        expected={"@type": "xsd:integer"},
    )
