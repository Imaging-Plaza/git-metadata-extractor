"""Shared HTTP transport for the compatible per-source retrieval interfaces."""

from __future__ import annotations

import logging
import os

from git_metadata_extractor.providers.sources_service import (
    SourcesClient,
    current_sources_client,
)

logger = logging.getLogger(__name__)


class RemoteRetrievalProvider:
    provider_name: str

    def __init__(self, *, client: SourcesClient | None = None) -> None:
        self._client = client

    async def _call(self, method: str, payload: dict, *, empty):
        client = self._client or current_sources_client()
        if client is None:
            logger.warning(
                "Sources retrieval unavailable: SOURCES_API_URL is not configured",
            )
            return empty
        result = await client.retrieve(self.provider_name, method, payload)
        if result.status != "ok" or not isinstance(result.data, type(empty)):
            logger.warning(
                "Sources retrieval %s.%s unavailable (%s)",
                self.provider_name,
                method,
                result.status,
            )
            if self.provider_name == "federated":
                return {
                    **empty,
                    "errors": {"_": result.error or "Sources retrieval unavailable"},
                }
            return empty
        return result.data


def build_remote_provider(provider_type, client=None):
    gate = f"V2_{provider_type.provider_name.upper()}_RAG_ENABLED"
    if os.getenv(gate, "true").strip().lower() not in {
        "1",
        "true",
        "t",
        "yes",
        "y",
        "on",
    }:
        return None
    client = client or current_sources_client()
    return provider_type(client=client) if client is not None else None
