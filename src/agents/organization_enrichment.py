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

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..data_models import GitAuthor, Organization, Person, SoftwareSourceCode

# Configure logging
logger = logging.getLogger(__name__)

# Semaphore to limit concurrent Selenium sessions
# Set to 1 to prevent memory issues (each browser instance uses 500MB-1GB)
# Only increase if using Selenium Grid with multiple nodes AND have sufficient RAM
_MAX_SELENIUM_SESSIONS = int(os.getenv("MAX_SELENIUM_SESSIONS", "1"))
_selenium_semaphore = asyncio.Semaphore(_MAX_SELENIUM_SESSIONS)


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
    output_type=OrganizationEnrichmentResult,
    system_prompt="""You are an expert at identifying and standardizing organization information from software repository metadata.

Your task is to analyze:
1. Git author email addresses (look for institutional domains)
2. Author affiliations from ORCID records
3. Existing organization mentions
4. Any other contextual information
5. Git commit dates per author to assess temporal affiliation patterns
6. ORCID affiliation start/end dates when available

For each organization you identify:
- Use the extract_domain_from_email tool first to check if the email domain is known
- **If the domain is unknown**, use the search_ror tool (PREFERRED) to find the organization
- The search_web tool (DuckDuckGo) is available for additional context and includes retry logic for reliability
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
    logger.info(f"🔍 Agent tool called: search_ror('{query}')")
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

            logger.info(f"✓ ROR search for '{query}' returned {len(results)} results")
            return json.dumps(results, indent=2)

    except Exception as e:
        logger.error(f"✗ Error searching ROR for '{query}': {e}")
        return json.dumps({"error": str(e)})


@agent.tool
async def search_web(
    ctx: RunContext[OrganizationAnalysisContext],
    query: str,
) -> str:
    """
    Search DuckDuckGo for information about an organization using Selenium.
    Includes retry logic (up to 3 attempts) to handle transient failures.

    Args:
        ctx: The run context
        query: The search query about an organization

    Returns:
        Summary of search results from DuckDuckGo (JSON string)
    """
    logger.info(f"🔍 Agent tool called: search_web('{query}')")

    max_retries = 3
    retry_delay = 2  # seconds

    for attempt in range(1, max_retries + 1):
        try:
            result = await _search_duckduckgo_single_attempt(
                query,
                attempt,
                max_retries,
            )

            # Check if we got results
            result_data = json.loads(result)
            if result_data.get("results") and len(result_data["results"]) > 0:
                logger.info(
                    f"✓ DuckDuckGo search for '{query}' returned {len(result_data['results'])} results (attempt {attempt})",
                )
                return result

            # No results but no error - might retry
            if attempt < max_retries:
                logger.warning(
                    f"⚠ DuckDuckGo search for '{query}' returned no results (attempt {attempt}/{max_retries}), retrying in {retry_delay}s...",
                )
                await asyncio.sleep(retry_delay)
            else:
                logger.warning(
                    f"⚠ DuckDuckGo search for '{query}' returned no results after {max_retries} attempts",
                )
                return result

        except Exception as e:
            if attempt < max_retries:
                logger.warning(
                    f"⚠ Error on attempt {attempt}/{max_retries} for '{query}': {e}, retrying in {retry_delay}s...",
                )
                await asyncio.sleep(retry_delay)
            else:
                logger.error(
                    f"✗ Error searching DuckDuckGo for '{query}' after {max_retries} attempts: {e}",
                )
                return json.dumps({"error": str(e), "query": query})

    # Should never reach here, but just in case
    return json.dumps({"error": "Max retries exceeded", "query": query})


async def _search_duckduckgo_single_attempt(
    query: str,
    attempt: int,
    max_attempts: int,
) -> str:
    """
    Single attempt to search DuckDuckGo.

    Args:
        query: Search query
        attempt: Current attempt number
        max_attempts: Maximum number of attempts

    Returns:
        JSON string with search results
    """
    selenium_url = os.getenv(
        "SELENIUM_REMOTE_URL",
        "http://selenium-standalone-firefox:4444",
    )

    # Acquire semaphore to limit concurrent Selenium sessions
    async with _selenium_semaphore:
        logger.debug(
            f"🔒 Acquired Selenium semaphore for query: '{query}' (attempt {attempt})",
        )

        # Configure Firefox options
        options = Options()
        options.add_argument("--headless")
        options.set_preference(
            "general.useragent.override",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        driver = None
        try:
            # Connect to remote Selenium
            driver = webdriver.Remote(
                command_executor=selenium_url,
                options=options,
            )

            # Perform DuckDuckGo search
            search_query = quote_plus(query)
            search_url = f"https://duckduckgo.com/?q={search_query}"
            driver.get(search_url)

            # Wait for page to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body")),
            )

            # Give page time to render
            time.sleep(2)

            # Extract search results using DuckDuckGo selectors
            results = []

            # Try different result selectors (DuckDuckGo structure)
            result_selectors = [
                "article[data-testid='result']",  # Main results
                "div[data-testid='result']",  # Alternative
                "div.result",  # Older structure
            ]

            search_results = []
            for selector in result_selectors:
                search_results = driver.find_elements(By.CSS_SELECTOR, selector)
                if search_results:
                    logger.debug(
                        f"Found {len(search_results)} results using selector: {selector}",
                    )
                    break

            if not search_results:
                logger.debug(
                    f"No search results found for query: '{query}' (attempt {attempt})",
                )
                return json.dumps(
                    {
                        "query": query,
                        "results": [],
                        "note": "No results found",
                        "attempt": attempt,
                    },
                )

            # Extract details from top 5 results
            for result in search_results[:5]:
                try:
                    # Extract title
                    title = ""
                    title_selectors = [
                        "h2",
                        "a[data-testid='result-title-a']",
                        ".result__a",
                    ]
                    for sel in title_selectors:
                        try:
                            title_elem = result.find_element(By.CSS_SELECTOR, sel)
                            title = title_elem.text
                            if title:
                                break
                        except Exception:
                            continue

                    # Extract link
                    link = ""
                    link_selectors = [
                        "a[data-testid='result-title-a']",
                        "a.result__a",
                        "h2 a",
                    ]
                    for sel in link_selectors:
                        try:
                            link_elem = result.find_element(By.CSS_SELECTOR, sel)
                            link = link_elem.get_attribute("href")
                            if link:
                                break
                        except Exception:
                            continue

                    # Extract snippet
                    snippet = ""
                    snippet_selectors = [
                        "div[data-result='snippet']",
                        ".result__snippet",
                        "div.snippet",
                    ]
                    for sel in snippet_selectors:
                        try:
                            snippet_elem = result.find_element(By.CSS_SELECTOR, sel)
                            snippet = snippet_elem.text
                            if snippet:
                                break
                        except Exception:
                            continue

                    # Only add result if we got at least a title or link
                    if title or link:
                        results.append(
                            {
                                "title": title,
                                "link": link,
                                "snippet": snippet,
                            },
                        )

                except Exception as e:
                    logger.debug(f"Error processing individual result: {e}")
                    continue

            return json.dumps(
                {
                    "query": query,
                    "results": results,
                    "attempt": attempt,
                },
                indent=2,
            )

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            logger.debug(f"🔓 Released Selenium semaphore for query: '{query}'")


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
    logger.info(f"🔍 Agent tool called: extract_domain_from_email('{email}')")
    if not email or "@" not in email:
        logger.warning(f"⚠ Invalid email format: '{email}'")
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

    # If domain is unknown, provide guidance to search for it
    if not known_domains.get(domain):
        logger.info(f"⚠ Unknown domain '{domain}' - suggesting search")
        result["suggestion"] = (
            f"Domain '{domain}' is not in the known domains list. "
            f"Consider using search_ror('{domain}') to find the organization, "
            f"or search_web('{domain} organization') for more information."
        )
        result["note"] = "Unknown institutional domain - search recommended"
    else:
        logger.info(f"✓ Domain analysis for '{email}': {domain} (known)")

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
{
        json.dumps(
            [
                {
                    "name": a.name,
                    "email": a.email,
                    "commits": {
                        "total": a.commits.total if a.commits else 0,
                        "firstCommitDate": str(a.commits.firstCommitDate)
                        if a.commits and a.commits.firstCommitDate
                        else None,
                        "lastCommitDate": str(a.commits.lastCommitDate)
                        if a.commits and a.commits.lastCommitDate
                        else None,
                    },
                }
                for a in context.git_authors
            ],
            indent=2,
        )
    }

Authors with ORCID affiliations:
{
        json.dumps(
            [
                {
                    "name": a.name,
                    "orcidId": str(a.orcidId) if a.orcidId else None,
                    "affiliation": a.affiliation,
                }
                for a in context.authors
            ],
            indent=2,
        )
    }

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

    logger.info(f"🚀 Starting organization enrichment for {repository_url}")
    logger.info(
        f"📊 Input data: {len(context.git_authors)} git authors, {len(context.authors)} ORCID authors",
    )

    # Run the agent
    logger.info("🤖 Running PydanticAI agent...")
    result = await agent.run(prompt, deps=context)

    logger.info(f"✅ Organization enrichment completed for {repository_url}")
    logger.info(
        f"📍 Identified {len(result.output.organizations)} organizations",
    )
    logger.info(
        f"🎯 EPFL relation: {result.output.relatedToEPFL} (confidence: {result.output.relatedToEPFLConfidence:.2f})",
    )

    # Log organization details
    if result.output.organizations:
        logger.info("📋 Organizations found:")
        for i, org in enumerate(result.output.organizations, 1):
            org_name = org.legalName if hasattr(org, "legalName") else str(org)
            logger.info(f"  {i}. {org_name}")

    return result.output


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

                        from ..data_models import Commits

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
                        from ..data_models import Commits

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
{
        json.dumps(
            [
                {
                    "name": a.name,
                    "email": a.email,
                    "commits": {
                        "total": a.commits.total if a.commits else 0,
                        "firstCommitDate": str(a.commits.firstCommitDate)
                        if a.commits and a.commits.firstCommitDate
                        else None,
                        "lastCommitDate": str(a.commits.lastCommitDate)
                        if a.commits and a.commits.lastCommitDate
                        else None,
                    },
                }
                for a in context.git_authors
            ],
            indent=2,
        )
    }

Authors with ORCID affiliations:
{
        json.dumps(
            [
                {
                    "name": a.name,
                    "orcidId": str(a.orcidId) if a.orcidId else None,
                    "affiliation": a.affiliation,
                }
                for a in context.authors
            ],
            indent=2,
        )
    }

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

    logger.info(f"🚀 Starting organization enrichment from dict for {repository_url}")
    logger.info(
        f"📊 Input data: {len(git_authors)} git authors, {len(authors)} ORCID authors",
    )

    # Run the agent
    logger.info("🤖 Running PydanticAI agent...")
    result = await agent.run(prompt, deps=context)

    logger.info(f"✅ Organization enrichment completed for {repository_url}")
    logger.info(
        f"📍 Identified {len(result.output.organizations)} organizations",
    )
    logger.info(
        f"🎯 EPFL relation: {result.output.relatedToEPFL} (confidence: {result.output.relatedToEPFLConfidence:.2f})",
    )

    # Return as dictionary
    return result.output.model_dump()
