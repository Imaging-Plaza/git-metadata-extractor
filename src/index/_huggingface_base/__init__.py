"""Shared infrastructure for the per-entity HuggingFace index modules.

The five concrete modules (``huggingface_models``,
``huggingface_datasets``, ``huggingface_spaces``, ``huggingface_users``,
``huggingface_organizations``) follow the same naming convention as the
github_* indices: one platform-prefixed module per entity type, each
with its own DuckDB file + Qdrant collection.

This module owns the pieces that are identical across the five:

  - ``client.py`` — the ``HFClient`` wrapper around ``huggingface_hub.HfApi``.
    Re-used verbatim from the legacy ``src.index.huggingface.ingest.hf_client``,
    just relocated.
  - ``config_base.py`` — ``HFEntityIndexConfigBase`` Pydantic shape
    (rcp / huggingface / qdrant / chunking / paths). Concrete modules
    instantiate this with their own YAML + paths.

The cross-cutting infrastructure that already exists in
``_github_accounts_base`` is reused as-is:

  - ``paths_base.resolve_account_paths(...)`` for the
    ``<INDEX_DATA_DIR>/<subdir>/`` tree.
  - ``storage_base`` helpers (bootstrap, count, fetch, stream_unembedded,
    upsert_chunk).
  - ``embed_base.embed_accounts_async(...)`` with explicit
    ``min_card_chars`` (so the HF config's ``huggingface.min_card_chars``
    is passed in directly).
  - ``retrieval_base.account_semantic_search_async(...)`` for the
    embed → Qdrant → rerank → hydrate pipeline.

Nothing in the legacy ``src.index.huggingface`` module changes during
H1; the catch-all module continues to work. H2–H6 build the five new
modules against this base; H7 tears the catch-all down.
"""

from src.index._huggingface_base.client import HFClient
from src.index._huggingface_base.config_base import (
    HFEntityIndexConfigBase,
    load_hf_entity_config,
)

__all__ = [
    "HFClient",
    "HFEntityIndexConfigBase",
    "load_hf_entity_config",
]
