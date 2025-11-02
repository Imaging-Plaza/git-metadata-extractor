"""
Example: User Enrichment

This example demonstrates how to use the user enrichment module to get
more insights about repository authors and their affiliations.
"""

import asyncio
import json
from datetime import date

from src.agents import enrich_users, enrich_users_from_dict
from src.data_models import Commits, GitAuthor, Person


async def example_basic_enrichment():
    """Example of basic user enrichment with git authors"""

    print("=" * 80)
    print("Example 1: Basic User Enrichment")
    print("=" * 80)

    # Sample git authors with commit history
    git_authors = [
        GitAuthor(
            name="John Doe",
            email="john.doe@epfl.ch",
            commits=Commits(
                total=45,
                firstCommitDate=date(2022, 1, 15),
                lastCommitDate=date(2024, 3, 20),
            ),
        ),
        GitAuthor(
            name="Jane Smith",
            email="jane.smith@ethz.ch",
            commits=Commits(
                total=32,
                firstCommitDate=date(2021, 6, 10),
                lastCommitDate=date(2023, 11, 5),
            ),
        ),
        GitAuthor(
            name="Bob Wilson",
            email="bob@gmail.com",
            commits=Commits(
                total=12,
                firstCommitDate=date(2023, 2, 1),
                lastCommitDate=date(2023, 8, 15),
            ),
        ),
    ]

    # Existing author information (e.g., from ORCID)
    existing_authors = [
        Person(
            name="John Doe",
            orcidId="https://orcid.org/0000-0001-2345-6789",
            affiliation=["EPFL", "Swiss Data Science Center"],
        ),
    ]

    repository_url = "https://github.com/example/research-project"

    # Enrich user information
    result = await enrich_users(
        git_authors=git_authors,
        existing_authors=existing_authors,
        repository_url=repository_url,
    )

    print(f"\n✅ Enriched {len(result.enrichedAuthors)} authors\n")

    for author in result.enrichedAuthors:
        print(f"👤 {author.name}")
        print(f"   Email: {author.email or 'N/A'}")
        print(f"   ORCID: {author.orcidId or 'N/A'}")
        print(f"   Current Affiliation: {author.currentAffiliation or 'Unknown'}")
        print(
            f"   All Affiliations: {', '.join(author.affiliations) if author.affiliations else 'None'}",
        )
        print(f"   Confidence: {author.confidenceScore:.2f}")
        if author.contributionSummary:
            print(f"   Contribution: {author.contributionSummary}")
        print()

    print(f"📊 Summary: {result.summary}\n")


async def example_enrichment_from_dict():
    """Example of user enrichment from dictionary data (e.g., from API)"""

    print("=" * 80)
    print("Example 2: User Enrichment from Dictionary Data")
    print("=" * 80)

    # Sample data as dictionaries (as you might receive from an API)
    git_authors_data = [
        {
            "name": "Alice Johnson",
            "email": "alice@datascience.ch",
            "commits": {
                "total": 67,
                "firstCommitDate": "2020-09-01",
                "lastCommitDate": "2024-10-01",
            },
        },
        {
            "name": "Carlos Rodriguez",
            "email": "carlos.rodriguez@pasteur.fr",
            "commits": {
                "total": 28,
                "firstCommitDate": "2022-03-15",
                "lastCommitDate": "2024-05-20",
            },
        },
    ]

    existing_authors_data = [
        {
            "name": "Alice Johnson",
            "orcidId": "https://orcid.org/0000-0002-3456-7890",
            "affiliation": ["Swiss Data Science Center", "EPFL"],
        },
    ]

    repository_url = "https://github.com/example/another-project"

    # Enrich user information from dictionaries
    result_dict = await enrich_users_from_dict(
        git_authors_data=git_authors_data,
        existing_authors_data=existing_authors_data,
        repository_url=repository_url,
    )

    print("\n✅ Enrichment Result (as dictionary):\n")
    print(json.dumps(result_dict, indent=2))


async def example_minimal_enrichment():
    """Example with minimal information (just names and emails)"""

    print("=" * 80)
    print("Example 3: Minimal Information Enrichment")
    print("=" * 80)

    # Minimal git authors (no ORCID, basic commit info)
    git_authors = [
        GitAuthor(
            name="Unknown Contributor",
            email="contributor@unknown-domain.com",
            commits=Commits(total=5),
        ),
    ]

    repository_url = "https://github.com/example/small-project"

    # Enrich with minimal information
    result = await enrich_users(
        git_authors=git_authors,
        existing_authors=[],
        repository_url=repository_url,
    )

    print(f"\n✅ Enriched {len(result.enrichedAuthors)} authors\n")

    for author in result.enrichedAuthors:
        print(f"👤 {author.name}")
        print(f"   Email: {author.email or 'N/A'}")
        print(f"   Confidence: {author.confidenceScore:.2f}")
        print("   Note: Low confidence due to limited information available")
        print()


async def main():
    """Run all examples"""

    print("\n" + "=" * 80)
    print("USER ENRICHMENT EXAMPLES")
    print("=" * 80 + "\n")

    # Run examples
    await example_basic_enrichment()
    print("\n" + "-" * 80 + "\n")

    await example_enrichment_from_dict()
    print("\n" + "-" * 80 + "\n")

    await example_minimal_enrichment()

    print("\n" + "=" * 80)
    print("Examples completed!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    # Note: To run this example, you need:
    # 1. OPENAI_API_KEY environment variable set
    # 2. Selenium server running (for web search)
    # 3. MODEL environment variable (optional, defaults to gpt-4o-mini)

    asyncio.run(main())
