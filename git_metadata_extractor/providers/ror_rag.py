"""HTTP retrieval adapter; indexing and storage belong to the sources service."""

from __future__ import annotations

from typing import Any, Literal

from git_metadata_extractor.providers._remote_retrieval import (
    RemoteRetrievalProvider,
    build_remote_provider,
)

ScopeMode = Literal["epfl_ethz", "switzerland", "europe", "worldwide"]
_DEFAULT_TOP_K = 10


class RorRagProvider(RemoteRetrievalProvider):
    provider_name = "ror"

    async def search(
        self,
        query: str,
        *,
        scope_mode: ScopeMode = "worldwide",
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "search",
            {
                "query": query,
                "scope_mode": scope_mode,
                "top_k": top_k,
                "filters": filters,
                "rerank": rerank,
            },
            empty=[],
        )


def build_default_provider(client=None) -> RorRagProvider | None:
    return build_remote_provider(RorRagProvider, client)
