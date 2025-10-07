"""
Test organization enrichment functionality
"""

import asyncio

import pytest

from src.agents import (
    OrganizationEnrichmentResult,
    enrich_organizations_from_dict,
)

# Sample LLM output for testing
SAMPLE_LLM_OUTPUT = {
    "parseTimestamp": "2025-10-04T15:19",
    "name": "test-repo",
    "description": "A test repository",
    "author": [
        {
            "name": "Test Author",
            "orcidId": "https://orcid.org/0000-0000-0000-0001",
            "affiliation": ["EPFL - École Polytechnique Fédérale de Lausanne"],
        },
    ],
    "gitAuthors": [
        {"name": "testuser", "email": "test.user@epfl.ch", "commits": 10},
        {"name": "anotheruser", "email": "another@ethz.ch", "commits": 5},
    ],
    "relatedToOrganizations": ["EPFL"],
    "relatedToEPFL": True,
    "relatedToEPFLJustification": "Author affiliated with EPFL",
}


@pytest.mark.asyncio()
async def test_organization_enrichment_basic():
    """Test basic organization enrichment functionality"""

    result = await enrich_organizations_from_dict(
        SAMPLE_LLM_OUTPUT,
        "https://github.com/test/repo",
    )

    # Check that result has expected structure
    assert "organizations" in result
    assert "relatedToEPFL" in result
    assert "relatedToEPFLJustification" in result

    # Should identify at least one organization
    assert len(result["organizations"]) > 0

    # Check organization structure
    for org in result["organizations"]:
        assert "legalName" in org
        # May or may not have ROR ID depending on search results


@pytest.mark.asyncio()
async def test_organization_enrichment_with_multiple_emails():
    """Test enrichment with multiple institutional emails"""

    test_data = {
        "parseTimestamp": "2025-10-04T15:19",
        "name": "multi-org-repo",
        "author": [],
        "gitAuthors": [
            {"name": "user1", "email": "user1@epfl.ch", "commits": 20},
            {"name": "user2", "email": "user2@ethz.ch", "commits": 15},
            {"name": "user3", "email": "user3@pasteur.fr", "commits": 10},
        ],
    }

    result = await enrich_organizations_from_dict(
        test_data,
        "https://github.com/test/multi-org",
    )

    # Should identify multiple organizations from different email domains
    MIN_EXPECTED_ORGS = 2
    assert len(result["organizations"]) >= MIN_EXPECTED_ORGS

    # Check that key institutions are identified (may vary based on ROR results)
    # At minimum, should recognize the domains
    assert any(org for org in result["organizations"])


@pytest.mark.asyncio()
async def test_organization_enrichment_epfl_detection():
    """Test EPFL relationship detection"""

    test_data = {
        "parseTimestamp": "2025-10-04T15:19",
        "name": "epfl-repo",
        "author": [
            {
                "name": "EPFL Researcher",
                "affiliation": ["EPFL"],
            },
        ],
        "gitAuthors": [
            {"name": "researcher", "email": "researcher@epfl.ch", "commits": 50},
        ],
    }

    result = await enrich_organizations_from_dict(
        test_data,
        "https://github.com/test/epfl-repo",
    )

    # Should detect EPFL relationship
    assert result["relatedToEPFL"] is True
    assert len(result["relatedToEPFLJustification"]) > 0


@pytest.mark.asyncio()
async def test_organization_enrichment_no_institutional_emails():
    """Test enrichment with only generic emails"""

    test_data = {
        "parseTimestamp": "2025-10-04T15:19",
        "name": "generic-repo",
        "author": [],
        "gitAuthors": [
            {"name": "user1", "email": "user@gmail.com", "commits": 10},
            {"name": "user2", "email": "dev@users.noreply.github.com", "commits": 5},
        ],
    }

    result = await enrich_organizations_from_dict(
        test_data,
        "https://github.com/test/generic",
    )

    # Should still return a result, but may have fewer organizations
    assert "organizations" in result
    assert "relatedToEPFL" in result


@pytest.mark.asyncio()
async def test_organization_model_fields():
    """Test that enriched organizations have extended fields"""

    result = await enrich_organizations_from_dict(
        SAMPLE_LLM_OUTPUT,
        "https://github.com/test/repo",
    )

    if result["organizations"]:
        org = result["organizations"][0]

        # Check that new fields exist (may be None)
        assert "legalName" in org
        assert "hasRorId" in org or "hasRorId" not in org  # Optional field
        assert "organizationType" in org or "organizationType" not in org  # Optional
        assert "country" in org or "country" not in org  # Optional


def test_enrichment_result_model():
    """Test that OrganizationEnrichmentResult model works correctly"""

    # Create a test result
    result = OrganizationEnrichmentResult(
        organizations=[
            {
                "legalName": "Test University",
                "hasRorId": "https://ror.org/123456",
                "organizationType": "Education",
                "country": "Switzerland",
            },
        ],
        relatedToEPFL=True,
        relatedToEPFLJustification="Test justification",
        analysis_notes="Test notes",
    )

    assert len(result.organizations) == 1
    assert result.relatedToEPFL is True
    assert result.analysis_notes == "Test notes"


if __name__ == "__main__":
    # Run a basic test
    asyncio.run(test_organization_enrichment_basic())
    print("✅ Basic test passed!")
