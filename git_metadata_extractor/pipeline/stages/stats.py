from __future__ import annotations

from typing import TYPE_CHECKING

from rdflib.namespace import RDF
from rdflib.term import URIRef

from git_metadata_extractor.api_models import V2Stats

if TYPE_CHECKING:
    from rdflib import Graph

DEFAULT_STATS_RUN_ID = "in-memory"


def _count_entities_from_graph(graph: Graph) -> int:
    subjects = {
        subject
        for subject in graph.subjects(RDF.type, None)
        if isinstance(subject, URIRef)
    }
    return len(subjects)


def compute_stats(
    *,
    graph: Graph | None = None,
    run_id: str | None = None,
    duration_ms: int = 0,
    stages_completed: list[str] | None = None,
    entities_count: int | None = None,
) -> V2Stats:
    triples_count = len(graph) if graph is not None else 0
    if entities_count is None:
        entities_count = _count_entities_from_graph(graph) if graph is not None else 0

    return V2Stats(
        entities_count=entities_count,
        triples_count=triples_count,
        run_id=run_id or DEFAULT_STATS_RUN_ID,
        duration_ms=duration_ms,
        stages_completed=stages_completed or [],
    )
