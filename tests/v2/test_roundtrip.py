# ruff: noqa: C901, PLR0912, PLC0206, PERF401, PLR2004
from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

REPO_ROOT = Path(__file__).resolve().parents[2]
JSON_DIR = REPO_ROOT / "dev" / "ontology-v2-json-response" / "a-001"
JSONLD_FILE = JSON_DIR / "test" / "jsonld_output.json"
EXPECTED_ENTITY_COUNT = 33
EXPECTED_ENTITY_TYPES = 6

ARRAY_PROPERTIES_BY_TYPE = {
    "schema:SoftwareSourceCode": {
        "schema:author",
        "pulse:discipline",
        "schema:programmingLanguage",
    },
    "schema:ScholarlyArticle": {"schema:author"},
    "org:Organization": {"org:hasUnit", "pulse:owns"},
    "schema:Person": {"org:hasMembership", "pulse:hasContribution"},
}
ARRAY_PROPERTIES = {
    "pulse:discipline",
    "schema:programmingLanguage",
    "org:hasUnit",
    "pulse:owns",
    "pulse:hasContribution",
    "org:hasMembership",
}

SHAPE_FILES = {
    "schema:Person": "pulse_PersonShape.json",
    "schema:SoftwareSourceCode": "pulse_RepositoryShape.json",
    "org:Organization": "pulse_OrganizationShape.json",
    "org:Membership": "pulse_MembershipShape.json",
    "pulse:Contribution": "pulse_ContributionShape.json",
    "schema:ScholarlyArticle": "pulse_ArticleShape.json",
}

ENVELOPE_FIELDS = {"shacl", "identifiers", "idSource"}

NAMESPACES = {
    "http://schema.org/": "schema:",
    "http://www.w3.org/ns/org#": "org:",
    "http://www.w3.org/2006/time#": "time:",
    "https://open-pulse.epfl.ch/ontology#": "pulse:",
    "http://www.wikidata.org/entity/": "wd:",
}


def _to_prefixed(iri: str, *, use_pulse_prefix: bool = True) -> str:
    namespaces = {
        "https://open-pulse.epfl.ch/ontology#": "pulse:",
        "http://www.wikidata.org/entity/": "wd:",
        "http://schema.org/": "schema:",
        "http://www.w3.org/ns/org#": "org:",
        "http://www.w3.org/2006/time#": "time:",
        "https://open-pulse.epfl.ch/data/": "",
    }
    for namespace, prefix in namespaces.items():
        if not iri.startswith(namespace):
            continue
        local_name = iri[len(namespace) :]
        if not prefix or not use_pulse_prefix:
            return local_name
        return prefix + local_name
    return iri


def _shorten_id(iri: str) -> str:
    pulse_ns = "https://open-pulse.epfl.ch/ontology#"
    data_ns = "https://open-pulse.epfl.ch/data/"
    if iri.startswith(data_ns):
        return iri[len(data_ns) :]
    if iri.startswith(pulse_ns):
        return iri[len(pulse_ns) :]
    return iri


def _rdf_value(obj: Any) -> Any:
    if isinstance(obj, Literal):
        if obj.datatype == XSD.integer:
            return int(obj)
        if obj.datatype in (XSD.date, XSD.dateTime):
            return str(obj)
        return str(obj)
    if isinstance(obj, URIRef):
        return _to_prefixed(str(obj), use_pulse_prefix=True)
    return str(obj)


def _reconstruct_from_graph(graph: Graph) -> dict[str, list[dict[str, Any]]]:
    entities: dict[str, dict[str, Any]] = {}
    entity_types: dict[str, str] = {}

    for subject, _, object_value in graph.triples((None, RDF.type, None)):
        subject_str = str(subject)
        prefixed_type = _to_prefixed(str(object_value))
        if prefixed_type.startswith("http://www.w3.org/"):
            continue
        if "Shape" in str(object_value) or "Enumeration" in str(object_value):
            continue
        if str(object_value) in {
            "http://www.w3.org/2002/07/owl#Ontology",
            "http://www.w3.org/2000/01/rdf-schema#Class",
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#Property",
        }:
            continue
        entity_types[subject_str] = prefixed_type
        entities.setdefault(subject_str, {})

    for subject_str in entities:
        subject_uri = URIRef(subject_str)
        for predicate, object_value in graph.predicate_objects(subject_uri):
            predicate_str = str(predicate)
            if predicate_str == str(RDF.type):
                continue
            prefixed_property = _to_prefixed(predicate_str)
            value = _rdf_value(object_value)
            if prefixed_property in entities[subject_str]:
                existing = entities[subject_str][prefixed_property]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    entities[subject_str][prefixed_property] = [existing, value]
                continue

            entity_type = entity_types.get(subject_str)
            type_specific_arrays = (
                ARRAY_PROPERTIES_BY_TYPE.get(entity_type, set())
                if entity_type is not None
                else set()
            )
            if (
                prefixed_property in type_specific_arrays
                or prefixed_property in ARRAY_PROPERTIES
            ):
                entities[subject_str][prefixed_property] = [value]
            else:
                entities[subject_str][prefixed_property] = value

    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for subject_str, props in entities.items():
        entity_type = entity_types[subject_str]
        node = {
            "id": _shorten_id(subject_str),
            "type": entity_type,
        }
        node.update(props)
        by_type[entity_type].append(node)

    return dict(by_type)


def _normalise_for_comparison(obj: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in obj.items():
        if key in ENVELOPE_FIELDS or value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            result[key] = sorted(str(item) for item in value)
            continue
        if isinstance(value, str) and value.endswith("+00:00"):
            result[key] = value[:-6] + "Z"
            continue
        result[key] = value
    return result


def _compare_entities(
    original: dict[str, Any],
    reconstructed: dict[str, Any],
    entity_id: str,
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    original_normalized = _normalise_for_comparison(original)
    reconstructed_normalized = _normalise_for_comparison(reconstructed)

    original_keys = set(original_normalized)
    reconstructed_keys = set(reconstructed_normalized)

    for key in sorted(original_keys - reconstructed_keys):
        differences.append(
            {
                "entity": entity_id,
                "property": key,
                "type": "lost_property",
                "original": original_normalized[key],
                "reconstructed": None,
            },
        )

    for key in sorted(reconstructed_keys - original_keys):
        differences.append(
            {
                "entity": entity_id,
                "property": key,
                "type": "extra_property",
                "original": None,
                "reconstructed": reconstructed_normalized[key],
            },
        )

    for key in sorted(original_keys & reconstructed_keys):
        original_value = original_normalized[key]
        reconstructed_value = reconstructed_normalized[key]
        if isinstance(original_value, list) and isinstance(reconstructed_value, list):
            if original_value != reconstructed_value:
                differences.append(
                    {
                        "entity": entity_id,
                        "property": key,
                        "type": "value_mismatch",
                        "original": original_value,
                        "reconstructed": reconstructed_value,
                    },
                )
            continue
        if str(original_value) != str(reconstructed_value):
            differences.append(
                {
                    "entity": entity_id,
                    "property": key,
                    "type": "value_mismatch",
                    "original": original_value,
                    "reconstructed": reconstructed_value,
                },
            )

    return differences


def _load_original_entities() -> dict[str, list[dict[str, Any]]]:
    loaded: dict[str, list[dict[str, Any]]] = {}
    for entity_type, file_name in SHAPE_FILES.items():
        payload = json.loads((JSON_DIR / file_name).read_text(encoding="utf-8"))
        if isinstance(payload, list):
            loaded[entity_type] = [item for item in payload if isinstance(item, dict)]
    return loaded


def _run_comparison(
    original_by_type: dict[str, list[dict[str, Any]]],
    reconstructed_by_type: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    differences: list[dict[str, Any]] = []
    matched_count = 0
    unmatched_count = 0

    for entity_type, originals in original_by_type.items():
        reconstructed_lookup = {
            entity["id"]: entity
            for entity in reconstructed_by_type.get(entity_type, [])
            if isinstance(entity.get("id"), str)
        }
        for original in originals:
            entity_id = str(original.get("id", "unknown"))
            reconstructed = reconstructed_lookup.get(entity_id)
            if reconstructed is None:
                differences.append(
                    {
                        "entity": entity_id,
                        "property": None,
                        "type": "missing_entity",
                        "original": entity_type,
                        "reconstructed": None,
                    },
                )
                unmatched_count += 1
                continue
            entity_diffs = _compare_entities(original, reconstructed, entity_id)
            if entity_diffs:
                differences.extend(entity_diffs)
            else:
                matched_count += 1

    return {
        "matched": matched_count,
        "unmatched": unmatched_count,
        "differences": differences,
    }


def _parse_roundtrip_graph() -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(JSONLD_FILE.read_text(encoding="utf-8"))
    graph = Graph()
    graph.parse(data=json.dumps(payload), format="json-ld")
    return _reconstruct_from_graph(graph)


def _difference_message(differences: list[dict[str, Any]]) -> str:
    if not differences:
        return ""
    preview = "\n".join(str(entry) for entry in differences[:5])
    return f"Roundtrip mismatches ({len(differences)}):\n{preview}"


def test_roundtrip_preserves_all_mock_entities_without_differences() -> None:
    reconstructed = _parse_roundtrip_graph()
    reconstructed_count = sum(len(entities) for entities in reconstructed.values())
    comparison = _run_comparison(_load_original_entities(), reconstructed)

    assert len(reconstructed) == EXPECTED_ENTITY_TYPES
    assert reconstructed_count == EXPECTED_ENTITY_COUNT
    assert comparison["matched"] == EXPECTED_ENTITY_COUNT
    assert comparison["unmatched"] == 0
    assert comparison["differences"] == [], _difference_message(comparison["differences"])


def test_roundtrip_preserves_arrays_numeric_fields_and_datetimes() -> None:
    reconstructed = _parse_roundtrip_graph()
    repositories = reconstructed["schema:SoftwareSourceCode"]
    repository = next(
        entity for entity in repositories if entity["id"] == "EPFL-ENAC/geodata-toolkit"
    )

    assert sorted(repository["pulse:discipline"]) == ["wd:Q21201", "wd:Q8434"]
    assert sorted(repository["schema:programmingLanguage"]) == ["Python", "TypeScript"]
    assert isinstance(repository["pulse:githubRepoStars"], int)
    assert repository["pulse:githubRepoStars"] == 45
    assert repository["schema:dateCreated"].replace("+00:00", "Z") == "2023-03-15T10:30:00Z"


def test_roundtrip_comparison_reports_field_level_differences() -> None:
    originals = _load_original_entities()
    reconstructed = deepcopy(originals)
    reconstructed["schema:Person"][0]["schema:name"] = "Changed Name"

    comparison = _run_comparison(originals, reconstructed)
    mismatch = next(
        difference
        for difference in comparison["differences"]
        if difference["type"] == "value_mismatch"
    )
    assert mismatch["entity"] == originals["schema:Person"][0]["id"]
    assert mismatch["property"] == "schema:name"
