"""Build a `RealORCIDProvider` configured for the indexer.

We reuse the production-grade ORCID provider already maintained at
`src/v2/ingest/providers/orcid_provider.py` rather than reinventing
record fetch + expanded-search.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.v2.ingest.providers.orcid_provider import RealORCIDProvider

if TYPE_CHECKING:
    from src.index.orcid.config import OrcidIndexConfig


def build_orcid_provider(config: OrcidIndexConfig) -> RealORCIDProvider:
    """Construct an ORCID provider wired to this indexer's config."""
    return RealORCIDProvider(
        base_url=config.orcid.base_url,
        timeout=config.orcid.timeout_seconds,
    )
