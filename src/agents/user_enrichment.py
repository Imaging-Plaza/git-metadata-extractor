"""
User Enrichment Module

This module uses PydanticAI to perform a second-pass analysis on repository metadata
to identify and enrich user/author information, particularly focusing on their affiliations.
It analyzes:
- Git author names and emails
- ORCID records and affiliations
- Temporal patterns of contributions

The agent uses tools to:
- Search the web (DuckDuckGo) for additional context about users and their affiliations
- Query ORCID API for author information
- Analyze commit patterns to understand affiliation over time
"""

# ruff: noqa: E501, G004, ARG001, BLE001, S112, S110, C901, PLR0912, PLR0915, PLR2004, N815, PERF203, DTZ007, PLC0415, TRY400

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..data_models import GitAuthor, Person

# Configure logging
logger = logging.getLogger(__name__)

# Semaphore to limit concurrent Selenium sessions
# Set to 1 to prevent memory issues (each browser instance uses 500MB-1GB)
# Only increase if using Selenium Grid with multiple nodes AND have sufficient RAM
_MAX_SELENIUM_SESSIONS = int(os.getenv("MAX_SELENIUM_SESSIONS", "1"))
_selenium_semaphore = asyncio.Semaphore(_MAX_SELENIUM_SESSIONS)


class EnrichedAuthor(BaseModel):
    """Enriched author information"""

    name: str = Field(description="Author's name")
    email: Optional[str] = Field(description="Author's email address", default=None)
    orcidId: Optional[str] = Field(
        description="Author's ORCID identifier",
        default=None,
    )
    affiliations: list[str] = Field(
        description="List of all identified affiliations (current and historical)",
        default_factory=list,
    )
    currentAffiliation: Optional[str] = Field(
        description="Most recent or current affiliation",
        default=None,
    )
    affiliationHistory: list[dict[str, Any]] = Field(
        description="Temporal affiliation information with start/end dates when available",
        default_factory=list,
    )
    contributionSummary: Optional[str] = Field(
        description="Summary of the author's contributions to the repository",
        default=None,
    )
    confidenceScore: float = Field(
        description="Confidence score (0.0 to 1.0) for the enriched information",
        default=0.0,
    )
    additionalInfo: Optional[str] = Field(
        description="Additional biographical or professional information found",
        default=None,
    )


class UserEnrichmentResult(BaseModel):
    """Result of user enrichment analysis"""

    enrichedAuthors: list[EnrichedAuthor] = Field(
        description="List of enriched author information",
        default_factory=list,
    )
    summary: str = Field(
        description="Overall summary of the author affiliations and patterns",
    )


class UserAnalysisContext(BaseModel):
    """Context provided to the agent for analysis"""

    repository_url: str
    git_authors: list[GitAuthor]
    existing_authors: list[Person]


# Initialize the agent with OpenAI model
agent = Agent(
    model=f"openai:{os.getenv('MODEL', 'gpt-4o-mini')}",
    output_type=UserEnrichmentResult,
    system_prompt="""You are an expert at identifying and enriching author/user information from software repository metadata.

Your task is to analyze:
1. Git author information (name, email, commit history)
2. Existing ORCID records and affiliations
3. Temporal patterns of contributions (commit dates)
4. Email domains to infer institutional affiliations

For each author you analyze:
- Use the search_orcid tool to find ORCID records if not already available
- Use the search_web tool (DuckDuckGo) to find additional information about the author's affiliations
- Analyze email domains to infer institutional affiliations
- Look at commit dates to understand temporal affiliation patterns
- Identify both current and historical affiliations
- **Provide a confidence score (0.0 to 1.0)** for the enriched information based on:
  * Quality and completeness of sources (ORCID = high, institutional email = high, web search = moderate)
  * Consistency across multiple sources
  * Temporal alignment between commit dates and known affiliation periods
  * Amount and recency of contribution to the repository

Pay special attention to:
- Different name variations (e.g., "John Smith", "J. Smith", "Smith, John")
- Institutional email domains (e.g., @epfl.ch, @ethz.ch, @university.edu)
- Affiliation changes over time
- ORCID affiliation start/end dates aligned with commit patterns
- Active vs. historical contributors

For affiliation history:
- Extract temporal information when available (start/end dates)
- Align affiliation periods with commit activity
- Identify transitions between institutions
- Note if an author's commits align with specific affiliation periods

Confidence Scoring Guidelines:
- 0.9-1.0: Strong evidence (ORCID + institutional email + recent activity)
- 0.7-0.89: Good evidence (ORCID or institutional email + significant commits)
- 0.5-0.69: Moderate evidence (partial information + some commits)
- 0.3-0.49: Weak evidence (limited information or old/few commits)
- 0.0-0.29: Very weak or speculative evidence

Provide a summary that:
- Highlights the diversity of affiliations
- Identifies main contributing institutions
- Notes temporal patterns (e.g., "primarily EPFL authors from 2020-2023")
- Mentions any interesting collaboration patterns

Be thorough and use the tools available to you to gather and verify author information.""",
)


@agent.tool
async def search_orcid(
    ctx: RunContext[UserAnalysisContext],
    author_name: str,
    email: Optional[str] = None,
) -> str:
    """
    Search the ORCID API for author information.

    Args:
        ctx: The run context
        author_name: The author's name to search for
        email: Optional email address to help narrow the search

    Returns:
        JSON string with ORCID search results including ORCID IDs, names, and affiliations
    """
    logger.info(f"🔍 Agent tool called: search_orcid('{author_name}', '{email}')")

    try:
        # Build search query
        query_parts = []

        # Add name to query
        if author_name:
            # Try to parse first and last name
            name_parts = author_name.strip().split()
            if len(name_parts) >= 2:
                given_name = name_parts[0]
                family_name = " ".join(name_parts[1:])
                query_parts.append(f"given-names:{given_name}")
                query_parts.append(f"family-name:{family_name}")
            else:
                query_parts.append(f"family-name:{author_name}")

        # Add email to query if provided
        if email:
            query_parts.append(f"email:{email}")

        if not query_parts:
            return json.dumps({"error": "No search criteria provided"})

        query = " AND ".join(query_parts)

        async with httpx.AsyncClient() as client:
            headers = {
                "Accept": "application/json",
            }
            response = await client.get(
                "https://pub.orcid.org/v3.0/search/",
                params={"q": query},
                headers=headers,
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

            # Extract relevant information from results
            results = []
            num_found = data.get("num-found", 0)

            if num_found == 0:
                logger.info(f"⚠ No ORCID records found for '{author_name}'")
                return json.dumps({"query": query, "results": [], "num_found": 0})

            for result in data.get("result", [])[:5]:  # Top 5 results
                orcid_id = result.get("orcid-identifier", {}).get("path")

                # Get basic info from search result
                person_info = {
                    "orcid_id": f"https://orcid.org/{orcid_id}" if orcid_id else None,
                    "given_names": result.get("given-names"),
                    "family_name": result.get("family-name"),
                    "credit_name": result.get("credit-name"),
                }

                # Note: Full affiliation details require a separate API call to /v3.0/{orcid}/employments
                # For now, we'll just indicate that affiliations are available
                if orcid_id:
                    person_info[
                        "note"
                    ] = f"Full affiliation details available at https://pub.orcid.org/v3.0/{orcid_id}/employments"

                results.append(person_info)

            logger.info(
                f"✓ ORCID search for '{author_name}' returned {len(results)} results",
            )
            return json.dumps(
                {
                    "query": query,
                    "results": results,
                    "num_found": num_found,
                },
                indent=2,
            )

    except Exception as e:
        logger.error(f"✗ Error searching ORCID for '{author_name}': {e}")
        return json.dumps({"error": str(e)})


@agent.tool
async def search_web(
    ctx: RunContext[UserAnalysisContext],
    query: str,
) -> str:
    """
    Search DuckDuckGo for information about a person using Selenium.
    Includes retry logic (up to 3 attempts) to handle transient failures.

    Args:
        ctx: The run context
        query: The search query about a person (e.g., "John Smith EPFL researcher")

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
    ctx: RunContext[UserAnalysisContext],
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
        },
        "ethz.ch": {
            "organization": "ETH Zürich",
            "acronym": "ETH",
            "type": "university",
            "country": "Switzerland",
        },
        "pasteur.fr": {
            "organization": "Institut Pasteur",
            "type": "research institute",
            "country": "France",
        },
        "unil.ch": {
            "organization": "Université de Lausanne",
            "type": "university",
            "country": "Switzerland",
        },
        "datascience.ch": {
            "organization": "Swiss Data Science Center",
            "type": "research institute",
            "country": "Switzerland",
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
            f"Consider using search_web('{domain} organization') for more information."
        )
        result["note"] = "Unknown institutional domain - search recommended"
    else:
        logger.info(f"✓ Domain analysis for '{email}': {domain} (known)")

    return json.dumps(result, indent=2)


async def enrich_users(
    git_authors: list[GitAuthor],
    existing_authors: list[Person],
    repository_url: str,
) -> UserEnrichmentResult:
    """
    Enrich user/author information from repository metadata using PydanticAI agent.

    Args:
        git_authors: List of git authors with commit history
        existing_authors: List of existing Person objects (potentially from ORCID)
        repository_url: The repository URL

    Returns:
        Enriched user information
    """
    # Prepare context for the agent
    context = UserAnalysisContext(
        repository_url=repository_url,
        git_authors=git_authors,
        existing_authors=existing_authors,
    )

    # Prepare the prompt for the agent
    prompt = f"""Analyze the following repository authors and enrich their information, particularly their affiliations.

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

Existing Author Information (from ORCID):
{
        json.dumps(
            [
                {
                    "name": a.name,
                    "orcidId": str(a.orcidId) if a.orcidId else None,
                    "affiliation": a.affiliation,
                }
                for a in context.existing_authors
            ],
            indent=2,
        )
    }

Please:
1. For each git author, analyze their email domain to infer affiliations
2. Match git authors with existing ORCID records when possible
3. Use search_orcid tool to find ORCID records for authors without them
4. Use search_web tool to find additional information about authors and their affiliations
5. Examine commit patterns (first/last commit dates) to understand temporal affiliations
6. Identify both current and historical affiliations for each author
7. Create a comprehensive affiliation history when temporal data is available
8. Provide a confidence score (0.0 to 1.0) for each enriched author based on:
   - Quality of sources (ORCID, institutional email, web search)
   - Consistency across sources
   - Amount and recency of contributions
   - Temporal alignment between commits and affiliation periods
9. Provide an overall summary of author affiliations and patterns

Focus on understanding:
- Who are the main contributors and where are they affiliated?
- Are there patterns in affiliations over time?
- Which institutions are most represented?
- Are there active vs. historical contributors?
"""

    logger.info(f"🚀 Starting user enrichment for {repository_url}")
    logger.info(
        f"📊 Input data: {len(context.git_authors)} git authors, {len(context.existing_authors)} existing author records",
    )

    # Run the agent
    logger.info("🤖 Running PydanticAI agent...")
    result = await agent.run(prompt, deps=context)

    logger.info(f"✅ User enrichment completed for {repository_url}")
    logger.info(
        f"👥 Enriched {len(result.output.enrichedAuthors)} authors",
    )

    # Log author details
    if result.output.enrichedAuthors:
        logger.info("📋 Enriched authors:")
        for i, author in enumerate(result.output.enrichedAuthors, 1):
            logger.info(
                f"  {i}. {author.name} - {author.currentAffiliation or 'Unknown affiliation'} "
                f"(confidence: {author.confidenceScore:.2f})",
            )

    return result.output


async def enrich_users_from_dict(
    git_authors_data: list[dict[str, Any]],
    existing_authors_data: list[dict[str, Any]],
    repository_url: str,
) -> dict[str, Any]:
    """
    Convenience function to enrich users from dictionaries (e.g., from API response).

    Args:
        git_authors_data: List of git author dictionaries
        existing_authors_data: List of existing author dictionaries
        repository_url: The repository URL

    Returns:
        Dictionary with enriched user information
    """
    from datetime import datetime

    from ..data_models import Commits

    # Convert dictionaries to model objects
    git_authors = []
    for ga_data in git_authors_data:
        if isinstance(ga_data, dict):
            # Handle Commits object conversion
            commits_data = ga_data.get("commits")
            if commits_data:
                if isinstance(commits_data, dict):
                    # Parse dates from strings if needed
                    first_date = commits_data.get("firstCommitDate")
                    last_date = commits_data.get("lastCommitDate")

                    if isinstance(first_date, str):
                        first_date = datetime.strptime(first_date, "%Y-%m-%d").date()
                    if isinstance(last_date, str):
                        last_date = datetime.strptime(last_date, "%Y-%m-%d").date()

                    commits_obj = Commits(
                        total=commits_data.get("total"),
                        firstCommitDate=first_date,
                        lastCommitDate=last_date,
                    )
                    ga_with_commits = {
                        "name": ga_data.get("name"),
                        "email": ga_data.get("email"),
                        "commits": commits_obj,
                    }
                    git_authors.append(GitAuthor(**ga_with_commits))
                else:
                    # Legacy format where commits is just a number
                    commits_obj = Commits(total=commits_data)
                    ga_with_commits = {
                        "name": ga_data.get("name"),
                        "email": ga_data.get("email"),
                        "commits": commits_obj,
                    }
                    git_authors.append(GitAuthor(**ga_with_commits))
            else:
                git_authors.append(GitAuthor(**ga_data))

    # Convert existing authors
    existing_authors = []
    for author_data in existing_authors_data:
        if isinstance(author_data, dict):
            # Handle empty orcidId strings
            author_copy = author_data.copy()
            if "orcidId" in author_copy and not author_copy["orcidId"]:
                author_copy["orcidId"] = None
            existing_authors.append(Person(**author_copy))

    # Call the main enrichment function
    result = await enrich_users(git_authors, existing_authors, repository_url)

    # Return as dictionary
    return result.model_dump()
