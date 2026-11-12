"""Fetch public ORCID employment and education records with the V2 provider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from git_metadata_extractor.providers.cache import (
    ProviderCache,
    reset_cache_refresh,
    set_cache_refresh,
)
from git_metadata_extractor.providers.orcid_provider import RealORCIDProvider

if TYPE_CHECKING:
    from git_metadata_extractor.providers.base import ORCIDRecord


def fetch_affiliations(
    orcid: str,
    cache_path: Path,
    *,
    refresh: bool = False,
) -> ORCIDRecord:
    """Accept a bare ORCID or URL; the provider validates its checksum."""
    provider = RealORCIDProvider(cache=ProviderCache(cache_path))
    token = set_cache_refresh(refresh)
    try:
        return provider.get_person_by_orcid(orcid)
    finally:
        reset_cache_refresh(token)
        provider.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("orcid", nargs="?", default="0000-0002-1126-1535")
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=Path("data/examples/orcid.db"),
    )
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    record = fetch_affiliations(args.orcid, args.cache_path, refresh=args.refresh)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
