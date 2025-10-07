"""
Example: Organization Enrichment

This example demonstrates how to use the organization enrichment feature
to analyze repository metadata and identify related organizations with
standardized ROR information.
"""

import asyncio
import json

from src.agents import enrich_organizations_from_dict

# Example output from the initial LLM analysis
EXAMPLE_LLM_OUTPUT = {
    "parseTimestamp": "2025-10-04T15:19",
    "name": "gimie",
    "description": "Extract linked metadata from repositories",
    "author": [
        {
            "name": "Cyril Matthey-Doret",
            "orcidId": "https://orcid.org/0000-0002-1126-1535",
            "affiliation": [
                "Swiss Data Science Center",
                "EPFL - École Polytechnique Fédérale de Lausanne",
                "Institut Pasteur",
                "Université de Lausanne",
            ],
        },
        {
            "name": "Sabine Maennel",
            "orcidId": "https://orcid.org/0009-0001-3022-8239",
            "affiliation": ["Swiss Data Science Center"],
        },
        {
            "name": "Robin Franken",
            "orcidId": "https://orcid.org/0009-0008-0143-9118",
            "affiliation": [
                "Swiss Data Science Center",
                "EPFL - École Polytechnique Fédérale de Lausanne",
            ],
        },
    ],
    "relatedToOrganizations": ["Swiss Data Science Center"],
    "relatedToOrganizationJustification": [
        "All authors are affiliated with Swiss Data Science Center.",
    ],
    "relatedToEPFL": True,
    "relatedToEPFLJustification": "Swiss Data Science Center is established by EPFL and ETH Zürich.",
    "gitAuthors": [
        {
            "name": "cmdoret",
            "email": "cyril.mattheydoret@gmail.com",
            "commits": {
                "total": 178,
                "firstCommitDate": "2021-05-12",
                "lastCommitDate": "2024-09-15",
            },
        },
        {
            "name": "Sabine Maennel",
            "email": "sabine.maennel@gmail.com",
            "commits": {
                "total": 62,
                "firstCommitDate": "2022-03-10",
                "lastCommitDate": "2024-08-20",
            },
        },
        {
            "name": "rmfranken",
            "email": "robin.franken@epfl.ch",
            "commits": {
                "total": 53,
                "firstCommitDate": "2022-06-01",
                "lastCommitDate": "2024-09-30",
            },
        },
        {
            "name": "Robin Franken",
            "email": "77491494+rmfranken@users.noreply.github.com",
            "commits": {
                "total": 37,
                "firstCommitDate": "2022-07-15",
                "lastCommitDate": "2024-09-25",
            },
        },
        {
            "name": "Martin Fontanet",
            "email": "martin.fontanet@epfl.ch",
            "commits": {
                "total": 26,
                "firstCommitDate": "2023-01-10",
                "lastCommitDate": "2024-05-20",
            },
        },
        {
            "name": "Cyril Matthey-Doret",
            "email": "cyril.matthey-doret@epfl.ch",
            "commits": {
                "total": 23,
                "firstCommitDate": "2021-06-01",
                "lastCommitDate": "2023-12-15",
            },
        },
        {
            "name": "Laure Vancau",
            "email": "laure.vancauwenberghe@epfl.ch",
            "commits": {
                "total": 13,
                "firstCommitDate": "2023-03-01",
                "lastCommitDate": "2024-04-10",
            },
        },
    ],
}


async def main():
    """Run the organization enrichment example"""

    print("=" * 80)
    print("Organization Enrichment Example")
    print("=" * 80)
    print()

    repository_url = "https://github.com/sdsc-ordes/gimie"

    print(f"Repository: {repository_url}")
    print()
    print("Input metadata summary:")
    print(f"  - Authors: {len(EXAMPLE_LLM_OUTPUT['author'])}")
    print(f"  - Git authors: {len(EXAMPLE_LLM_OUTPUT['gitAuthors'])}")
    print(f"  - Existing organizations: {EXAMPLE_LLM_OUTPUT['relatedToOrganizations']}")
    print()

    print("Running organization enrichment...")
    print("This will:")
    print("  1. Analyze git author email domains")
    print("  2. Extract affiliations from ORCID records")
    print("  3. Examine commit date patterns per author")
    print("  4. Query ROR API for standardized organization information")
    print("  5. Identify organizational hierarchies")
    print("  6. Assess EPFL relationship with confidence scoring")
    print("  7. Provide confidence scores for each organization attribution")
    print()

    try:
        enrichment_result = await enrich_organizations_from_dict(
            EXAMPLE_LLM_OUTPUT,
            repository_url,
        )

        print("=" * 80)
        print("Enrichment Results")
        print("=" * 80)
        print()

        print(f"Organizations identified: {len(enrichment_result['organizations'])}")
        print()

        for i, org in enumerate(enrichment_result["organizations"], 1):
            print(f"Organization {i}:")
            print(f"  Legal Name: {org.get('legalName', 'N/A')}")
            print(f"  ROR ID: {org.get('hasRorId', 'N/A')}")
            print(f"  Type: {org.get('organizationType', 'N/A')}")
            print(f"  Country: {org.get('country', 'N/A')}")
            print(f"  Website: {org.get('website', 'N/A')}")
            if org.get("confidenceOfAttriution"):
                print(f"  Confidence Score: {org['confidenceOfAttriution']:.2f}")
            if org.get("alternateNames"):
                print(f"  Alternate Names: {', '.join(org['alternateNames'])}")
            if org.get("parentOrganization"):
                print(f"  Parent Organization: {org['parentOrganization']}")
            print()

        print(f"Related to EPFL: {enrichment_result['relatedToEPFL']}")
        if enrichment_result.get("relatedToEPFLConfidence"):
            print(
                f"EPFL Confidence Score: {enrichment_result['relatedToEPFLConfidence']:.2f}",
            )
        print(f"Justification: {enrichment_result['relatedToEPFLJustification']}")
        print()

        if enrichment_result.get("analysis_notes"):
            print(f"Analysis Notes: {enrichment_result['analysis_notes']}")
            print()

        # Save results to file
        output_file = "organization_enrichment_result.json"
        with open(output_file, "w") as f:
            json.dump(enrichment_result, f, indent=2)
        print(f"Full results saved to: {output_file}")

    except Exception as e:
        print(f"Error during enrichment: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
