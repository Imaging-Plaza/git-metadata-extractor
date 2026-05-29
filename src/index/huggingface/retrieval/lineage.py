"""Walk the HuggingFace `base_models` graph in both directions.

Each model's `base_models` column is a JSON list of parent `repo_id`s
(the model(s) it was fine-tuned from). We use DuckDB for the walk:

* **Ancestors** — recursive lookup of `models.base_models` for each
  current node, up to `depth` hops. Most chains are shallow (≤2).
* **Descendants** — `WHERE base_models @> '[repo_id]'`. Same depth limit.

Returns a graph in adjacency-list form. Cheap (~ms) for typical EPFL repos
since the local DB is small.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.index.huggingface.storage.duckdb_store import HuggingFaceStore

LOGGER = logging.getLogger(__name__)


def compute_lineage(
    repo_id: str,
    *,
    store: HuggingFaceStore,
    depth: int = 3,
) -> dict[str, Any]:
    """Return the ancestor + descendant subgraphs of `repo_id` up to `depth` hops.

    Output:
        {
          "root":        "<repo_id>",
          "ancestors":   { "level_1": [...], "level_2": [...], ... },
          "descendants": { "level_1": [...], "level_2": [...], ... },
          "edges":       [{"from": ..., "to": ...}, ...],   # to=parent, from=child
        }
    """
    seen: set[str] = {repo_id}
    edges: list[dict[str, str]] = []
    ancestors: dict[str, list[dict[str, Any]]] = {}
    descendants: dict[str, list[dict[str, Any]]] = {}

    # ---- Ancestors (walk `base_models` upward) ------------------------------
    frontier: deque[str] = deque([repo_id])
    for level in range(1, depth + 1):
        next_frontier: deque[str] = deque()
        ancestors[f"level_{level}"] = []
        while frontier:
            node = frontier.popleft()
            row = store.fetch_repo("models", node)
            if row is None:
                continue
            parents = _coerce_repo_id_list(row.get("base_models"))
            for parent in parents:
                edges.append({"from": node, "to": parent})
                if parent in seen:
                    continue
                seen.add(parent)
                parent_row = store.fetch_repo("models", parent) or {"repo_id": parent}
                ancestors[f"level_{level}"].append(_thin_record(parent_row))
                next_frontier.append(parent)
        frontier = next_frontier
        if not frontier:
            break

    # ---- Descendants (walk `base_models` downward) --------------------------
    seen.clear()
    seen.add(repo_id)
    frontier = deque([repo_id])
    for level in range(1, depth + 1):
        next_frontier = deque()
        descendants[f"level_{level}"] = []
        while frontier:
            node = frontier.popleft()
            children = _find_children(store, node)
            for child in children:
                edges.append({"from": child["repo_id"], "to": node})
                if child["repo_id"] in seen:
                    continue
                seen.add(child["repo_id"])
                descendants[f"level_{level}"].append(_thin_record(child))
                next_frontier.append(child["repo_id"])
        frontier = next_frontier
        if not frontier:
            break

    # Drop empty levels.
    ancestors = {k: v for k, v in ancestors.items() if v}
    descendants = {k: v for k, v in descendants.items() if v}

    return {
        "root": repo_id,
        "ancestors": ancestors,
        "descendants": descendants,
        "edges": edges,
        "depth": depth,
    }


def _find_children(store: HuggingFaceStore, parent_id: str) -> list[dict[str, Any]]:
    """Return rows whose `base_models` list contains `parent_id`.

    DuckDB JSON-array containment is best expressed as a string scan since
    `base_models` is stored as a JSON-encoded VARCHAR.
    """
    needle = json.dumps(parent_id, ensure_ascii=False)
    sql = (
        "SELECT * FROM models "
        "WHERE base_models IS NOT NULL "
        "  AND base_models != 'null' "
        "  AND base_models != '[]' "
        "  AND base_models LIKE ? "
        "ORDER BY downloads DESC NULLS LAST"
    )
    cur = store.connect().execute(sql, [f"%{needle}%"])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def _coerce_repo_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [str(v) for v in parsed if v]
    return []


def _thin_record(row: dict[str, Any]) -> dict[str, Any]:
    """Trim a model row to fields useful for lineage display."""
    keep = (
        "repo_id", "author", "pipeline_tag", "library_name",
        "license", "downloads", "likes", "last_modified",
    )
    return {k: row.get(k) for k in keep if k in row}


__all__ = ["compute_lineage"]
