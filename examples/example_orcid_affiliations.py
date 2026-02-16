"""
Example: Enriching author metadata with ORCID affiliations

This example demonstrates how to:
1. Parse ORCID IDs from URLs
2. Fetch affiliations (organization names only) from ORCID
3. Enrich author objects with ORCID data
4. Use caching to reduce API calls

The data is automatically cached with a 14-day TTL.
"""

import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.utils import (
    enrich_author_with_orcid,
    extract_orcid_id,
    get_orcid_affiliations,
)


def example_extract_orcid_id():
    """Example: Extract ORCID ID from URL"""
    print("\n" + "=" * 60)
    print("Example 1: Extracting ORCID ID from URL")
    print("=" * 60)

    orcid_urls = [
        "https://orcid.org/0000-0002-1126-1535",
        "0000-0009-0001-3022-8239",
        "http://orcid.org/0000-0002-1126-1535",
    ]

    for url in orcid_urls:
        orcid_id = extract_orcid_id(url)
        print(f"Input:  {url}")
        print(f"Output: {orcid_id}\n")


def example_get_affiliations():
    """Example: Get affiliations from ORCID"""
    print("\n" + "=" * 60)
    print("Example 2: Fetching affiliations from ORCID")
    print("=" * 60)

    # Example ORCID IDs
    orcid_ids = [
        "0000-0002-1126-1535",  # Cyril Matthey-Doret
        "0000-0009-0001-3022-8239",  # Sabine Maennel
    ]

    for orcid_id in orcid_ids:
        print(f"\nFetching affiliations for ORCID: {orcid_id}")
        affiliations = get_orcid_affiliations(orcid_id, use_cache=True)

        if affiliations:
            print(f"Found {len(affiliations)} affiliation(s):")
            for i, affiliation in enumerate(affiliations, 1):
                print(f"  {i}. {affiliation}")
        else:
            print("  No affiliations found")


def example_enrich_authors():
    """Example: Enrich author objects with ORCID affiliations"""
    print("\n" + "=" * 60)
    print("Example 3: Enriching author objects with ORCID data")
    print("=" * 60)

    # Example author objects (as they might appear in repository metadata)
    authors = [
        {
            "name": "Cyril Matthey-Doret",
            "orcidId": "https://orcid.org/0000-0002-1126-1535",
        },
        {
            "name": "Sabine Maennel",
            "orcidId": "https://orcid.org/0009-0001-3022-8239",
        },
        {
            "name": "John Doe",
            # No ORCID ID - will not be enriched
        },
    ]

    print("\nOriginal authors:")
    print(json.dumps(authors, indent=2))

    # Enrich authors with ORCID affiliations
    enriched_authors = [enrich_author_with_orcid(author) for author in authors]

    print("\nEnriched authors:")
    print(json.dumps(enriched_authors, indent=2))


def example_cache_usage():
    """Example: Demonstrating cache usage"""
    print("\n" + "=" * 60)
    print("Example 4: Cache usage")
    print("=" * 60)

    orcid_id = "0000-0002-1126-1535"

    print("\nFirst call (will fetch from ORCID and cache):")
    affiliations1 = get_orcid_affiliations(orcid_id, use_cache=True)
    print(f"Affiliations: {affiliations1}")

    print("\nSecond call (will use cached data):")
    affiliations2 = get_orcid_affiliations(orcid_id, use_cache=True)
    print(f"Affiliations: {affiliations2}")

    print("\nThird call (force refresh, bypass cache):")
    affiliations3 = get_orcid_affiliations(orcid_id, use_cache=False)
    print(f"Affiliations: {affiliations3}")


def main():
    """Run all examples"""
    print("\n" + "=" * 60)
    print("ORCID Affiliations Examples")
    print("=" * 60)

    try:
        # Run examples
        example_extract_orcid_id()
        example_get_affiliations()
        example_enrich_authors()
        example_cache_usage()

        print("\n" + "=" * 60)
        print("✅ All examples completed successfully!")
        print("=" * 60)
        print("\nNote: ORCID data is cached with a 14-day TTL to reduce API calls.")
        print("Use force_refresh=True or use_cache=False to bypass the cache.")

    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
