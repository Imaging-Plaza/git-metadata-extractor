"""HuggingFace Papers index — DuckDB + Qdrant catalog of HF Papers cards.

Each ingested paper is one DuckDB row (`papers` table, keyed on
arXiv id) and one or more Qdrant points (collection
``huggingface_papers``) holding an embedding of title + abstract +
author names. The wire side hits the HF Papers REST endpoint
``GET /api/papers/{arxiv_id}`` directly — the existing HF Python
library doesn't expose papers.

Use cases:

  - Semantic search ("find papers about graph neural networks for
    materials science") returning HF-curated paper cards.
  - Disambiguation: given an arXiv id mentioned in a CITATION.cff or
    README, fetch HF's enriched view (AI summary, linked models /
    datasets, upvote signal).

Sibling to ``src.index.github_users`` / ``src.index.github_organizations``:
each owns its own DuckDB file + Qdrant collection; cross-cutting
infrastructure (chunk schema, embed loop, semantic search) is shared
via lightweight helpers.
"""

from src.index.huggingface_papers.config import (
    HuggingFacePapersIndexConfig,
    load_config,
)
from src.index.huggingface_papers.models import PaperRecord
from src.index.huggingface_papers.paths import (
    HuggingFacePapersPaths,
    get_huggingface_papers_paths,
)
from src.index.huggingface_papers.storage.duckdb_store import (
    HuggingFacePapersStore,
)

__all__ = [
    "HuggingFacePapersIndexConfig",
    "HuggingFacePapersPaths",
    "HuggingFacePapersStore",
    "PaperRecord",
    "get_huggingface_papers_paths",
    "load_config",
]
