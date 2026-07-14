# ruff: noqa: INP001

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rdflib import RDF, Graph, Namespace

SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
PULSE = Namespace("https://open-pulse.epfl.ch/ontology#")


def _compact_identifier(uri: str) -> str:
    if uri.startswith("http://www.wikidata.org/entity/"):
        return f"wd:{uri.rsplit('/', maxsplit=1)[-1]}"
    if uri.startswith("https://open-pulse.epfl.ch/ontology#"):
        return f"pulse:{uri.rsplit('#', maxsplit=1)[-1]}"
    return uri


def default_ttl_path() -> Path:
    # Single source of truth: resolve via the runtime helper so this dev
    # script and the SHACL validator always read the same packaged TTL
    # (the file moved from `dev/` into `git_metadata_extractor/validation/` so it ships in
    # the image — see git_metadata_extractor.validation.ontology).
    from git_metadata_extractor.validation.ontology import ontology_ttl_path

    return ontology_ttl_path()


def _extract_entries(graph: Graph, enum_type_iri: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for subject in sorted(set(graph.subjects(RDF.type, enum_type_iri)), key=str):
        label_literal = next(graph.objects(subject, SKOS.prefLabel), None)
        label = str(label_literal) if label_literal is not None else ""
        subject_uri = str(subject)
        entries.append(
            {
                "id": _compact_identifier(subject_uri),
                "label": label,
                "uri": subject_uri,
            },
        )
    return entries


def extract_enums_from_ttl(ttl_path: Path) -> dict[str, list[dict[str, str]]]:
    graph = Graph()
    graph.parse(ttl_path, format="turtle")
    return {
        "disciplines": _extract_entries(graph, PULSE.DisciplineEnumeration),
        "repository_types": _extract_entries(graph, PULSE.RepositoryTypeEnumeration),
        "organization_types": _extract_entries(graph, PULSE.OrganizationTypeEnumeration),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract enum values from Open Pulse TTL ontology")
    parser.add_argument(
        "--ttl-path",
        type=Path,
        default=default_ttl_path(),
        help="Path to open-pulse-ontology-v2.1.2.ttl (or any later revision)",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON output indentation")
    args = parser.parse_args()

    payload = extract_enums_from_ttl(args.ttl_path)
    print(json.dumps(payload, indent=args.indent, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
