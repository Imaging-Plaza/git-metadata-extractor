"""Shared RCP (Research Compute Platform) client wrappers.

The RCP exposes two OpenAI-compatible endpoints we rely on across
every index:

  - ``RCPEmbeddingClient`` (``/embeddings``) — turns text into vectors
    during ingest, before Qdrant upsert.
  - ``RCPRerankerClient`` (``/rerank``) — re-orders Qdrant candidates
    at search time.

Previously this code lived under ``src.index.openalex.embed.rcp_client``
because the openalex module was written first, but it has nothing
to do with OpenAlex semantically. K1 relocated it here and
generalised the constructor to accept any config matching
``RCPConfigProtocol``.

Modules that want stricter typing can subclass — see
``src/index/orcid/embed/rcp_client.py`` for the canonical
re-typing pattern.
"""

from src.index._rcp.embed_client import (
    RCPConfigProtocol,
    RCPEmbeddingClient,
    RCPEmbeddingError,
)
from src.index._rcp.reranker_client import RCPRerankerClient

__all__ = [
    "RCPConfigProtocol",
    "RCPEmbeddingClient",
    "RCPEmbeddingError",
    "RCPRerankerClient",
]
