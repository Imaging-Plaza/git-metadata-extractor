"""
Organization Enrichment Module

This module uses PydanticAI to perform a second-pass analysis on repository metadata
to identify, standardize, and enrich organization information. It analyzes:
- Git author emails
- Author affiliations from ORCID
- Any other metadata that can reveal organizational relationships

The agent uses tools to:
- Query ROR (Research Organization Registry) for standardized organization names and IDs
- Search the web for additional context
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from .models import GitAuthor, Organization, Person, SoftwareSourceCode

# Configure logging
logger = logging.getLogger(__name__)


class OrganizationEnrichmentResult(BaseModel):
    """Result of organization enrichment analysis"""

    organizations: List[Organization] = Field(
        description="List of all identified organizations with standardized information",
    )
    relatedToEPFL: bool = Field(description="Whether the repository is related to EPFL")
    relatedToEPFLConfidence: float = Field(
        description="Confidence score (0.0 to 1.0) for EPFL relationship",
    )
    relatedToEPFLJustification: str = Field(
        description="Detailed justification for EPFL relationship",
    )


class OrganizationAnalysisContext(BaseModel):
    """Context provided to the agent for analysis"""

    repository_url: str
    git_authors: List[GitAuthor]
    authors: List[Person]
    existing_organizations: List[str]
    existing_justification: Optional[str] = None
    existing_epfl_relation: Optional[bool] = None
    existing_epfl_justification: Optional[str] = None


# Initialize the agent with OpenAI model
# The agent will analyze organization information and use tools as needed
agent = Agent(
    model=f"openai:{os.getenv('MODEL', 'gpt-4o-mini')}",
    result_type=OrganizationEnrichmentResult,
    system_prompt="""You are an expert at identifying and standardizing organization information from software repository metadata.

Your task is to analyze:
1. Git author email addresses (look for institutional domains)
2. Author affiliations from ORCID records
3. Existing organization mentions
4. Any other contextual information
5. Git commit dates per author to assess temporal affiliation patterns
6. ORCID affiliation start/end dates when available

For each organization you identify:
- Use the search_ror tool to find the official ROR entry and get standardized naming
- Identify the organization type (university, research institute, department, lab, company, etc.)
- For departments/labs, identify the parent organization
- Extract country and website information when available
- **Provide a confidence score (0.0 to 1.0)** for each organization attribution based on:
  * Strength of evidence (institutional email = high, ORCID affiliation = high, generic email = low)
  * Number of commits from authors affiliated with the organization
  * Temporal alignment between commit dates and ORCID affiliation periods
  * Consistency across multiple sources

Pay special attention to:
- Email domains (e.g., @epfl.ch, @ethz.ch, @pasteur.fr)
- Different variations of organization names (e.g., "EPFL", "École Polytechnique Fédérale de Lausanne", "Ecole Polytechnique Federale de Lausanne")
- Hierarchical relationships (e.g., "Swiss Data Science Center" is established by EPFL and ETH Zürich)
- Departments and labs within larger organizations

For EPFL relationship:
- Consider direct affiliations (authors with @epfl.ch emails, ORCID affiliations mentioning EPFL)
- Consider indirect relationships (Swiss Data Science Center, labs/departments at EPFL)
- **Provide a confidence score (0.0 to 1.0)** for EPFL relationship based on:
  * Number and percentage of commits from EPFL-affiliated authors
  * Temporal patterns: recent activity from EPFL authors vs. historical activity
  * Strength of affiliation evidence (institutional email vs. ORCID vs. inference)
  * Whether the repository is primarily developed by EPFL authors (>50% commits)
  * Alignment between author commit dates and their ORCID affiliation periods at EPFL
- Provide detailed justification with specific evidence including commit statistics and temporal patterns
- But please, provide a coherent confidence score for the EPFL relationship

Confidence Scoring Guidelines:
- 0.9-1.0: Strong evidence (institutional email + significant commits + temporal alignment)
- 0.7-0.89: Good evidence (institutional email or ORCID + moderate commits)
- 0.5-0.69: Moderate evidence (ORCID affiliation or some commits with institutional email)
- 0.3-0.49: Weak evidence (few commits or only indirect indicators)
- 0.0-0.29: Very weak or speculative evidence

Think that one author might have multiple affiliations over time. Look at the commit dates to see if they align with the affiliation periods.

Be thorough and use the tools available to you to verify and standardize organization information.""",
)


@agent.tool
async def search_ror(
    ctx: RunContext[OrganizationAnalysisContext],
    query: str,
) -> str:
    """
    Search the ROR (Research Organization Registry) API for organization information.

    Args:
        ctx: The run context
        query: The organization name or affiliation string to search for

    Returns:
        JSON string with ROR search results including standardized names, ROR IDs, types, countries, and websites
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.ror.org/organizations",
                params={"query": query},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

            # Extract relevant information from top results
            results = []
            for item in data.get("items", [])[:5]:  # Top 5 results
                org_info = {
                    "name": item.get("name"),
                    "ror_id": item.get("id"),
                    "types": item.get("types", []),
                    "country": item.get("country", {}).get("country_name"),
                    "aliases": item.get("aliases", []),
                    "acronyms": item.get("acronyms", []),
                    "links": item.get("links", []),
                    "relationships": [
                        {
                            "label": rel.get("label"),
                            "type": rel.get("type"),
                            "id": rel.get("id"),
                        }
                        for rel in item.get("relationships", [])
                    ],
                }
                results.append(org_info)

            logger.info(f"ROR search for '{query}' returned {len(results)} results")
            return json.dumps(results, indent=2)

    except Exception as e:
        logger.error(f"Error searching ROR for '{query}': {e}")
        return json.dumps({"error": str(e)})


@agent.tool
async def search_web(
    ctx: RunContext[OrganizationAnalysisContext],
    query: str,
) -> str:
    """
    Search the web for information about an organization.
    Uses DuckDuckGo as a simple search provider.

    Args:
        ctx: The run context
        query: The search query about an organization

    Returns:
        Summary of search results
    """
    try:
        # Use DuckDuckGo's instant answer API (no API key required)
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": query,
                    "format": "json",
                    "no_redirect": 1,
                },
                timeout=10.0,
            )
            response.raise_for_status()

            # Check if response has content before parsing
            if not response.text or response.text.strip() == "":
                logger.warning(
                    f"Web search for '{query}' returned empty response",
                )
                return json.dumps(
                    {
                        "abstract": "",
                        "abstract_source": "",
                        "abstract_url": "",
                        "related_topics": [],
                        "note": "No results found",
                    },
                )

            try:
                data = response.json()
            except json.JSONDecodeError:
                logger.warning(
                    f"Web search for '{query}' returned non-JSON response: {response.text[:100]}",
                )
                return json.dumps(
                    {
                        "abstract": "",
                        "abstract_source": "",
                        "abstract_url": "",
                        "related_topics": [],
                        "note": "Invalid response format",
                    },
                )

            result = {
                "abstract": data.get("Abstract", ""),
                "abstract_source": data.get("AbstractSource", ""),
                "abstract_url": data.get("AbstractURL", ""),
                "related_topics": [
                    {
                        "text": topic.get("Text", ""),
                        "url": topic.get("FirstURL", ""),
                    }
                    for topic in data.get("RelatedTopics", [])[:3]
                ],
            }

            logger.info(f"Web search for '{query}' completed")
            return json.dumps(result, indent=2)

    except Exception as e:
        logger.error(f"Error searching web for '{query}': {e}")
        return json.dumps({"error": str(e)})


@agent.tool
async def extract_domain_from_email(
    ctx: RunContext[OrganizationAnalysisContext],
    email: str,
) -> str:
    """
    Extract the domain from an email address and provide information about it.

    Args:
        ctx: The run context
        email: Email address

    Returns:
        Domain information including known organization associations
    """
    if not email or "@" not in email:
        return json.dumps({"error": "Invalid email format"})

    domain = email.split("@")[1].lower()

    # Known institutional domains
    known_domains = {
        "epfl.ch": {
            "organization": "École Polytechnique Fédérale de Lausanne",
            "acronym": "EPFL",
            "type": "university",
            "country": "Switzerland",
            "ror_id": "https://ror.org/02s376052",
        },
        "ethz.ch": {
            "organization": "ETH Zürich",
            "acronym": "ETH",
            "type": "university",
            "country": "Switzerland",
            "ror_id": "https://ror.org/05a28rw58",
        },
        "pasteur.fr": {
            "organization": "Institut Pasteur",
            "type": "research institute",
            "country": "France",
            "ror_id": "https://ror.org/0495gfr97",
        },
        "unil.ch": {
            "organization": "Université de Lausanne",
            "type": "university",
            "country": "Switzerland",
            "ror_id": "https://ror.org/019whta54",
        },
        "datascience.ch": {
            "organization": "Swiss Data Science Center",
            "type": "research institute",
            "country": "Switzerland",
            "ror_id": "https://ror.org/02hdt9m26",
            "parent_organizations": ["EPFL", "ETH Zürich"],
        },
    }

    result = {
        "domain": domain,
        "known_organization": known_domains.get(domain),
    }

    logger.info(f"Domain analysis for '{email}': {domain}")
    return json.dumps(result, indent=2)


async def enrich_organizations(
    repository_metadata: SoftwareSourceCode,
    repository_url: str,
) -> OrganizationEnrichmentResult:
    """
    Enrich organization information from repository metadata using PydanticAI agent.

    Args:
        repository_metadata: The initial LLM analysis result
        repository_url: The repository URL

    Returns:
        Enriched organization information
    """
    # Prepare context for the agent
    context = OrganizationAnalysisContext(
        repository_url=repository_url,
        git_authors=repository_metadata.gitAuthors or [],
        authors=[
            author
            for author in (repository_metadata.author or [])
            if isinstance(author, Person)
        ],
        existing_organizations=repository_metadata.relatedToOrganizations or [],
        existing_justification=(
            repository_metadata.relatedToOrganizationJustification[0]
            if repository_metadata.relatedToOrganizationJustification
            else None
        ),
        existing_epfl_relation=repository_metadata.relatedToEPFL,
        existing_epfl_justification=repository_metadata.relatedToEPFLJustification,
    )

    # Prepare the prompt for the agent
    prompt = f"""Analyze the following repository metadata and identify all related organizations.

Repository: {repository_url}

Git Authors (with emails and commit history):
{json.dumps([{
    "name": a.name,
    "email": a.email,
    "commits": {
        "total": a.commits.total if a.commits else 0,
        "firstCommitDate": str(a.commits.firstCommitDate) if a.commits and a.commits.firstCommitDate else None,
        "lastCommitDate": str(a.commits.lastCommitDate) if a.commits and a.commits.lastCommitDate else None
    }
} for a in context.git_authors], indent=2)}

Authors with ORCID affiliations:
{json.dumps([{"name": a.name, "orcidId": str(a.orcidId) if a.orcidId else None, "affiliation": a.affiliation} for a in context.authors], indent=2)}

Existing organization mentions: {context.existing_organizations}
Existing justification: {context.existing_justification}
Existing EPFL relation: {context.existing_epfl_relation}
Existing EPFL justification: {context.existing_epfl_justification}

Please:
1. Analyze all email domains from git authors
2. Review all affiliations from ORCID records
3. Examine commit patterns: look at the first and last commit dates per author to understand temporal affiliation
4. For each organization identified, use the search_ror tool to find standardized information
5. Identify all levels of organizations (universities, departments, labs, research centers, etc.)
6. Determine hierarchical relationships where applicable
7. Provide a comprehensive assessment of EPFL relationship with detailed evidence
8. Return a complete list of organizations with standardized ROR information where available
9. For each organization, provide a confidence score (0.0 to 1.0) based on:
   - Strength of evidence (institutional email vs. ORCID vs. inference)
   - Number and percentage of commits from affiliated authors
   - Temporal alignment between commit dates and affiliation periods
10. Provide an EPFL affiliation confidence score (0.0 to 1.0) considering:
    - Percentage of commits from EPFL-affiliated authors
    - Whether EPFL authors are still active (recent commits)
    - Strength of affiliation evidence across multiple authors
"""

    logger.info(f"Starting organization enrichment for {repository_url}")

    # Run the agent
    result = await agent.run(prompt, deps=context)

    logger.info(f"Organization enrichment completed for {repository_url}")
    logger.info(f"Identified {len(result.data.organizations)} organizations")

    return result.data


async def enrich_organizations_from_dict(
    llm_output: Dict[str, Any],
    repository_url: str,
) -> Dict[str, Any]:
    """
    Convenience function to enrich organizations from a dictionary (e.g., from API response).

    Args:
        llm_output: The initial LLM analysis output as a dictionary
        repository_url: The repository URL

    Returns:
        Dictionary with enriched organization information
    """
    # Extract relevant data without converting to SoftwareSourceCode
    # (since relatedToOrganizations is a list of strings, not Organization objects)

    git_authors = []
    if llm_output.get("gitAuthors"):
        for ga in llm_output["gitAuthors"]:
            if isinstance(ga, dict):
                # Handle Commits object conversion
                commits_data = ga.get("commits")
                if commits_data:
                    if isinstance(commits_data, dict):
                        # Parse dates from strings if needed
                        from datetime import datetime

                        first_date = commits_data.get("firstCommitDate")
                        last_date = commits_data.get("lastCommitDate")

                        if isinstance(first_date, str):
                            first_date = datetime.strptime(
                                first_date,
                                "%Y-%m-%d",
                            ).date()
                        if isinstance(last_date, str):
                            last_date = datetime.strptime(last_date, "%Y-%m-%d").date()

                        from .models import Commits

                        commits_obj = Commits(
                            total=commits_data.get("total"),
                            firstCommitDate=first_date,
                            lastCommitDate=last_date,
                        )
                        ga_with_commits = {
                            "name": ga.get("name"),
                            "email": ga.get("email"),
                            "commits": commits_obj,
                        }
                        git_authors.append(GitAuthor(**ga_with_commits))
                    else:
                        # Legacy format where commits is just a number
                        from .models import Commits

                        commits_obj = Commits(total=commits_data)
                        ga_with_commits = {
                            "name": ga.get("name"),
                            "email": ga.get("email"),
                            "commits": commits_obj,
                        }
                        git_authors.append(GitAuthor(**ga_with_commits))
                else:
                    git_authors.append(GitAuthor(**ga))

    authors = []
    if llm_output.get("author"):
        for author in llm_output["author"]:
            if isinstance(author, dict):
                # Check if it's a Person (has name, orcidId, or affiliation)
                if "name" in author or "orcidId" in author or "affiliation" in author:
                    # Handle empty orcidId strings
                    author_data = author.copy()
                    if "orcidId" in author_data and not author_data["orcidId"]:
                        author_data["orcidId"] = None
                    authors.append(Person(**author_data))

    # Extract existing organizations - handle both string and dict formats
    existing_orgs = []
    related_orgs = llm_output.get("relatedToOrganizations", [])
    if related_orgs:
        for org in related_orgs:
            if isinstance(org, str):
                # Simple string organization name
                existing_orgs.append(org)
            elif isinstance(org, dict):
                # Organization object - extract the legal name
                existing_orgs.append(org.get("legalName", str(org)))

    # Prepare context for the agent
    context = OrganizationAnalysisContext(
        repository_url=repository_url,
        git_authors=git_authors,
        authors=authors,
        existing_organizations=existing_orgs,
        existing_justification=(
            llm_output.get("relatedToOrganizationJustification", [None])[0]
            if llm_output.get("relatedToOrganizationJustification")
            else None
        ),
        existing_epfl_relation=llm_output.get("relatedToEPFL"),
        existing_epfl_justification=llm_output.get("relatedToEPFLJustification"),
    )

    # Prepare the prompt for the agent
    prompt = f"""Analyze the following repository metadata and identify all related organizations.

Repository: {repository_url}

Git Authors (with emails and commit history):
{json.dumps([{
    "name": a.name,
    "email": a.email,
    "commits": {
        "total": a.commits.total if a.commits else 0,
        "firstCommitDate": str(a.commits.firstCommitDate) if a.commits and a.commits.firstCommitDate else None,
        "lastCommitDate": str(a.commits.lastCommitDate) if a.commits and a.commits.lastCommitDate else None
    }
} for a in context.git_authors], indent=2)}

Authors with ORCID affiliations:
{json.dumps([{"name": a.name, "orcidId": str(a.orcidId) if a.orcidId else None, "affiliation": a.affiliation} for a in context.authors], indent=2)}

Existing organization mentions: {context.existing_organizations}
Existing justification: {context.existing_justification}
Existing EPFL relation: {context.existing_epfl_relation}
Existing EPFL justification: {context.existing_epfl_justification}

Please:
1. Analyze all email domains from git authors
2. Review all affiliations from ORCID records
3. Examine commit patterns: look at the first and last commit dates per author to understand temporal affiliation
4. For each organization identified, use the search_ror tool to find standardized information
5. Identify all levels of organizations (universities, departments, labs, research centers, etc.)
6. Determine hierarchical relationships where applicable
7. Provide a comprehensive assessment of EPFL relationship with detailed evidence
8. Return a complete list of organizations with standardized ROR information where available
9. For each organization, provide a confidence score (0.0 to 1.0) based on:
   - Strength of evidence (institutional email vs. ORCID vs. inference)
   - Number and percentage of commits from affiliated authors
   - Temporal alignment between commit dates and affiliation periods
10. Provide an EPFL affiliation confidence score (0.0 to 1.0) considering:
    - Percentage of commits from EPFL-affiliated authors
    - Whether EPFL authors are still active (recent commits)
    - Strength of affiliation evidence across multiple authors
"""

    logger.info(f"Starting organization enrichment for {repository_url}")

    # Run the agent
    result = await agent.run(prompt, deps=context)

    logger.info(f"Organization enrichment completed for {repository_url}")
    logger.info(f"Identified {len(result.data.organizations)} organizations")

    # Return as dictionary
    return result.data.model_dump()
