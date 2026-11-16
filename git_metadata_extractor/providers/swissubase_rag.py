"""HTTP retrieval adapter; indexing and storage belong to the sources service."""

from __future__ import annotations

from typing import Any

from git_metadata_extractor.providers._remote_retrieval import (
    RemoteRetrievalProvider,
    build_remote_provider,
)


class SwissubaseRagProvider(RemoteRetrievalProvider):
    provider_name = "swissubase"

    async def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "search",
            {"query": query, "top_k": top_k, "filters": filters, "rerank": rerank},
            empty=[],
        )


def build_default_provider(client=None) -> SwissubaseRagProvider | None:
    return build_remote_provider(SwissubaseRagProvider, client)
