from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pytest
from rdflib import Graph, Namespace
from rdflib.namespace import RDF, XSD

from git_metadata_extractor.validation.ontology import ontology_ttl_path

SH = Namespace("http://www.w3.org/ns/shacl#")
SCHEMA = Namespace("http://schema.org/")
ORG = Namespace("http://www.w3.org/ns/org#")
TIME = Namespace("http://www.w3.org/2006/time#")
PULSE = Namespace("https://open-pulse.epfl.ch/ontology#")
WD = Namespace("http://www.wikidata.org/entity/")

REPO_ROOT = Path(__file__).resolve().parents[2]
STRICT_SCHEMA_DIR = REPO_ROOT / "git_metadata_extractor" / "schema" / "json" / "strict"

SHAPE_SCHEMA_MAP = {
    "PersonShape": "person.schema.json",
    "RepositoryShape": "repository.schema.json",
    "OrganizationShape": "organization.schema.json",
    "MembershipShape": "membership.schema.json",
    "ContributionShape": "contribution.schema.json",
    "ArticleShape": "article.schema.json",
}

ENUM_FIELD_MAP = {
    "DisciplineEnumeration": ("RepositoryShape", "pulse:discipline"),
    "RepositoryTypeEnumeration": ("RepositoryShape", "pulse:repositoryType"),
    "OrganizationTypeEnumeration": ("OrganizationShape", "pulse:OrganizationType"),
}

ENUM_CLASSES = {
    "DisciplineEnumeration": PULSE.DisciplineEnumeration,
    "RepositoryTypeEnumeration": PULSE.RepositoryTypeEnumeration,
    "OrganizationTypeEnumeration": PULSE.OrganizationTypeEnumeration,
}

ENVELOPE_FIELDS = {"id", "type", "shacl", "identifiers", "idSource"}

PREFIX_MAP = {
    str(SCHEMA): "schema:",
    str(ORG): "org:",
    str(TIME): "time:",
    str(PULSE): "pulse:",
    str(WD): "wd:",
    str(XSD): "xsd:",
}

XSD_TO_JSON = {
    str(XSD.string): "string",
    str(XSD.integer): "integer",
    str(XSD.date): "string",
    str(XSD.dateTime): "string",
}


def _to_prefixed(iri: str) -> str:
    for namespace, prefix in PREFIX_MAP.items():
        if iri.startswith(namespace):
            return prefix + iri[len(namespace) :]
    return iri


@lru_cache(maxsize=1)
def _load_graph() -> Graph:
    graph = Graph()
    graph.parse(ontology_ttl_path(), format="turtle")
    return graph


@lru_cache(maxsize=1)
def _load_schemas() -> dict[str, dict]:
    return {
        shape_name: json.loads((STRICT_SCHEMA_DIR / file_name).read_text(encoding="utf-8"))
        for shape_name, file_name in SHAPE_SCHEMA_MAP.items()
    }


def _shape_properties() -> dict[str, dict[str, dict[str, str | int]]]:
    graph = _load_graph()
    properties_by_shape: dict[str, dict[str, dict[str, str | int]]] = {}

    for shape_node in graph.subjects(RDF.type, SH.NodeShape):
        shape_name = str(shape_node).split("#")[-1]
        if shape_name not in SHAPE_SCHEMA_MAP:
            continue

        shape_properties: dict[str, dict[str, str | int]] = {}
        for property_node in graph.objects(shape_node, SH.property):
            path_node = next(iter(graph.objects(property_node, SH.path)), None)
            if path_node is None:
                continue

            property_key = _to_prefixed(str(path_node))
            constraints: dict[str, str | int] = {}
            datatype_node = next(iter(graph.objects(property_node, SH.datatype)), None)
            if datatype_node is not None:
                constraints["datatype"] = str(datatype_node)
            pattern_node = next(iter(graph.objects(property_node, SH.pattern)), None)
            if pattern_node is not None:
                constraints["pattern"] = str(pattern_node)
            min_count_node = next(iter(graph.objects(property_node, SH.minCount)), None)
            if min_count_node is not None:
                constraints["minCount"] = int(str(min_count_node))
            shape_properties[property_key] = constraints

        properties_by_shape[shape_name] = shape_properties

    return properties_by_shape


def _schema_enum_values(schema: dict, field_name: str) -> set[str]:
    prop = schema["properties"][field_name]
    if "enum" in prop:
        return set(prop["enum"])
    return set(prop["items"]["enum"])


@pytest.mark.parametrize("shape_name", SHAPE_SCHEMA_MAP)
def test_ttl_shape_properties_exist_in_json_schema(shape_name: str) -> None:
    ttl_properties = set(_shape_properties()[shape_name])
    schema_properties = set(_load_schemas()[shape_name]["properties"]) - ENVELOPE_FIELDS
    assert ttl_properties <= schema_properties


@pytest.mark.parametrize("shape_name", SHAPE_SCHEMA_MAP)
def test_json_schema_properties_exist_in_ttl_shape(shape_name: str) -> None:
    ttl_properties = set(_shape_properties()[shape_name])
    schema_properties = set(_load_schemas()[shape_name]["properties"]) - ENVELOPE_FIELDS
    assert schema_properties <= ttl_properties


@pytest.mark.parametrize("enum_name", ENUM_CLASSES)
def test_ttl_and_json_schema_enums_are_consistent(enum_name: str) -> None:
    graph = _load_graph()
    shape_name, field_name = ENUM_FIELD_MAP[enum_name]
    ttl_values = {
        _to_prefixed(str(instance))
        for instance in graph.subjects(RDF.type, ENUM_CLASSES[enum_name])
    }
    json_values = _schema_enum_values(_load_schemas()[shape_name], field_name)
    assert ttl_values == json_values


@pytest.mark.parametrize("shape_name", SHAPE_SCHEMA_MAP)
def test_ttl_regex_patterns_match_json_schema(shape_name: str) -> None:
    schema = _load_schemas()[shape_name]
    for property_name, constraints in _shape_properties()[shape_name].items():
        ttl_pattern = constraints.get("pattern")
        if ttl_pattern is None:
            continue
        json_pattern = schema["properties"][property_name].get("pattern")
        assert json_pattern == ttl_pattern


@pytest.mark.parametrize("shape_name", SHAPE_SCHEMA_MAP)
def test_ttl_required_fields_match_json_required(shape_name: str) -> None:
    ttl_required = {
        property_name
        for property_name, constraints in _shape_properties()[shape_name].items()
        if int(constraints.get("minCount", 0)) >= 1
    }
    json_required = set(_load_schemas()[shape_name].get("required", [])) - ENVELOPE_FIELDS
    assert ttl_required == json_required


@pytest.mark.parametrize("shape_name", SHAPE_SCHEMA_MAP)
def test_ttl_datatypes_match_json_schema_types(shape_name: str) -> None:
    schema = _load_schemas()[shape_name]
    for property_name, constraints in _shape_properties()[shape_name].items():
        ttl_datatype = constraints.get("datatype")
        if ttl_datatype is None:
            continue
        expected_json_type = XSD_TO_JSON.get(str(ttl_datatype))
        if expected_json_type is None:
            continue

        json_property = schema["properties"][property_name]
        json_type = json_property.get("type")
        if json_type is None:
            continue

        json_types = set(json_type) if isinstance(json_type, list) else {json_type}
        if "array" in json_types:
            items_type = json_property.get("items", {}).get("type")
            if items_type is None:
                continue
            assert items_type == expected_json_type
            continue

        assert expected_json_type in json_types


def test_property_alignment_guard_fails_when_ttl_adds_unmapped_property() -> None:
    shape_name = "PersonShape"
    ttl_properties = set(_shape_properties()[shape_name]) | {"pulse:newProperty"}
    schema_properties = set(_load_schemas()[shape_name]["properties"]) - ENVELOPE_FIELDS
    with pytest.raises(AssertionError):
        assert ttl_properties <= schema_properties
