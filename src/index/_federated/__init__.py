"""Cross-index federated search and entity-lookup layer.

Sits above `src/index/{huggingface,openalex,infoscience,zenodo,orcid,ror}/`
and exposes uniform `gme search` / `gme entity` commands that fan out to
every registered index in parallel and merge results.

Adapter pattern: each index module gets a thin adapter under
`adapters/<name>.py` implementing `IndexAdapter`. The federated layer never
imports an index's internals directly — it goes through the adapter.
"""

from src.index._federated.registry import (
    REGISTRY,
    EntityRecord,
    Hit,
    IndexAdapter,
    register,
)

__all__ = ["REGISTRY", "EntityRecord", "Hit", "IndexAdapter", "register"]
