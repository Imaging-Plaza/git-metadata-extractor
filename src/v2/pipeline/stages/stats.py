from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rdflib.namespace import RDF
from rdflib.term import URIRef

from src.v2.api_models import V2Stats

if TYPE_CHECKING:
    from rdflib import Graph

    from src.v2.graph.models import Run
    from src.v2.graph.store import GraphStore

DEFAULT_STATS_RUN_ID = "graph-export"


def _count_entities_from_graph(graph: Graph) -> int:
    subjects = {
        subject
        for subject in graph.subjects(RDF.type, None)
        if isinstance(subject, URIRef)
    }
    return len(subjects)


def _stages_from_run_stats(stats: dict[str, Any]) -> list[str]:
    raw_stages = stats.get("stages_completed")
    if not isinstance(raw_stages, list):
        return []
    return [stage for stage in raw_stages if isinstance(stage, str)]


def _duration_ms_from_run(run: Run) -> int:
    if run.completed_at is not None:
        delta = run.completed_at - run.started_at
        return max(0, int(delta.total_seconds() * 1000))

    raw_duration = run.stats.get("duration_ms")
    if isinstance(raw_duration, int) and raw_duration >= 0:
        return raw_duration
    return 0


def compute_stats(
    store: GraphStore,
    run_id: str | None = None,
    graph: Graph | None = None,
) -> V2Stats:
    graph_for_stats = graph if graph is not None else store.get_rdf_graph()
    entities_count = _count_entities_from_graph(graph_for_stats)
    if graph is None and entities_count == 0:
        entities_count = len(store.get_all_entities())

    resolved_run_id = run_id or DEFAULT_STATS_RUN_ID
    duration_ms = 0
    stages_completed: list[str] = []

    if run_id is not None:
        run = store.get_run(run_id)
        if run is not None:
            duration_ms = _duration_ms_from_run(run)
            stages_completed = _stages_from_run_stats(run.stats)

    return V2Stats(
        entities_count=entities_count,
        triples_count=len(graph_for_stats),
        run_id=resolved_run_id,
        duration_ms=duration_ms,
        stages_completed=stages_completed,
    )
