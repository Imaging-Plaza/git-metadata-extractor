"""RCP embedding client for ORCID.

Re-exports the openalex implementation under a thin subclass that
re-types the constructor parameter for `OrcidIndexConfig`. The base class
only reads `config.rcp.*` and calls `config.require_rcp()`, both of
which `OrcidIndexConfig` exposes with the same shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from src.index.openalex.embed.rcp_client import RCPEmbeddingClient as _BaseEmbed

if TYPE_CHECKING:
    from src.index.orcid.config import OrcidIndexConfig


class RCPEmbeddingClient(_BaseEmbed):
    """Type-narrowed wrapper around the openalex RCP embed client."""

    def __init__(
        self,
        config: OrcidIndexConfig,
        *,
        batch_size: int | None = None,
        timeout_s: float | None = None,
    ) -> None:
        super().__init__(
            cast("Any", config),
            batch_size=batch_size,
            timeout_s=timeout_s,
        )
