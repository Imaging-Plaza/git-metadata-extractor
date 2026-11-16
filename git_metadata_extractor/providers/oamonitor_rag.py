"""HTTP retrieval adapter; indexing and storage belong to the sources service."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003 - runtime signature inspection
from typing import Any

from git_metadata_extractor.providers._remote_retrieval import (
    RemoteRetrievalProvider,
    build_remote_provider,
)

_DEFAULT_ENTITY = "journals"
_DEFAULT_TOP_K = 10


class OamonitorRagProvider(RemoteRetrievalProvider):
    provider_name = "oamonitor"

    async def search(
        self,
        query: str,
        *,
        entity_type: str = _DEFAULT_ENTITY,
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "search",
            {
                "query": query,
                "entity_type": entity_type,
                "top_k": top_k,
                "filters": filters,
                "rerank": rerank,
            },
            empty=[],
        )

    async def fetch_records(
        self, ids: Sequence[str], *, entity_type: str,
    ) -> list[dict[str, Any]]:
        return await self._call(
            "fetch_records", {"ids": ids, "entity_type": entity_type}, empty=[],
        )


def build_default_provider(client=None) -> OamonitorRagProvider | None:
    return build_remote_provider(OamonitorRagProvider, client)
