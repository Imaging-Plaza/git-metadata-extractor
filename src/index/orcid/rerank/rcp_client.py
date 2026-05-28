"""RCP reranker client for ORCID.

Thin subclass of the openalex implementation re-typed for `OrcidIndexConfig`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from src.index.openalex.rerank.rcp_client import RCPRerankerClient as _BaseRerank

if TYPE_CHECKING:
    from src.index.orcid.config import OrcidIndexConfig


class RCPRerankerClient(_BaseRerank):
    def __init__(
        self,
        config: OrcidIndexConfig,
        *,
        path: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        super().__init__(cast("Any", config), path=path, timeout_s=timeout_s)
