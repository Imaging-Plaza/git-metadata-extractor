from __future__ import annotations

from typing import TYPE_CHECKING

from src.v2.models import IntermediateEnvelope

if TYPE_CHECKING:
    from src.v2.graph.store import GraphStore


def assemble_intermediates(
    source_url: str | None,
    store: GraphStore,
    limit: int | None = None,
    *,
    run_id: str | None = None,
) -> list[IntermediateEnvelope]:
    rows = store.get_intermediates(
        source_url=source_url,
        run_id=run_id,
        limit=limit,
    )

    envelopes: list[IntermediateEnvelope] = []
    for row in rows:
        payload = row.get("data")
        if not isinstance(payload, dict):
            payload = {"value": payload}
        envelopes.append(
            IntermediateEnvelope(
                agent_name=str(row.get("agent_name", "")),
                run_id=str(row.get("run_id")) if row.get("run_id") is not None else None,
                timestamp=str(row.get("created_at", "")),
                data=payload,
            ),
        )

    return envelopes
