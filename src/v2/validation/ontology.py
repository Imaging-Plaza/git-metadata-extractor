from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from rdflib import Graph

ONTOLOGY_RELATIVE_PATH = Path("dev/ontology-v2-json-response/open-pulse-ontology-v2.0.1.ttl")


def ontology_ttl_path() -> Path:
    return Path(__file__).resolve().parents[3] / ONTOLOGY_RELATIVE_PATH


@lru_cache(maxsize=1)
def load_ontology_shapes_graph() -> Graph:
    path = ontology_ttl_path()
    if not path.exists():
        raise FileNotFoundError(path)

    graph = Graph()
    graph.parse(path, format="turtle")
    return graph
