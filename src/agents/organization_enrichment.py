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
from typing import Any, Dict
from urllib.parse import quote_plus

import httpx
from pydantic_ai import Agent, RunContext
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..context.infoscience import (
    get_author_publications_tool,
    search_infoscience_labs_tool,
    search_infoscience_publications_tool,
)
from ..data_models import (
    GitAuthor,
    OrganizationAnalysisContext,
    OrganizationEnrichmentResult,
    Person,
    SoftwareSourceCode,
)
from ..llm.model_config import (
    create_pydantic_ai_model,
    get_retry_delay,
    load_model_config,
    validate_config,
)
from .organization_prompts import (
    get_organization_enrichment_prompt,
    organization_enrichment_main_system_prompt,
)

# Configure logging
logger = logging.getLogger(__name__)

# Semaphore to limit concurrent Selenium sessions
# Set to 1 to prevent memory issues (each browser instance uses 500MB-1GB)
# Only increase if using Selenium Grid with multiple nodes AND have sufficient RAM
_MAX_SELENIUM_SESSIONS = int(os.getenv("MAX_SELENIUM_SESSIONS", "1"))
_selenium_semaphore = asyncio.Semaphore(_MAX_SELENIUM_SESSIONS)


# Load model configuration
org_enrichment_configs = load_model_config("run_organization_enrichment")

# Validate configurations
for config in org_enrichment_configs:
    if not validate_config(config):
        logger.error(f"Invalid configuration for organization enrichment: {config}")
        raise ValueError("Invalid model configuration")

# Agent cleanup tracking
_active_org_agents = []


# Create agent with first configuration
def create_organization_enrichment_agent(config: dict) -> Agent:
    """Create an organization enrichment agent from configuration."""
    model = create_pydantic_ai_model(config)

    # Define Infoscience tools for the organization agent
    infoscience_tools = [
        search_infoscience_labs_tool,
        search_infoscience_publications_tool,
        get_author_publications_tool,
    ]

    agent = Agent(
        model=model,
        output_type=OrganizationEnrichmentResult,
        system_prompt=organization_enrichment_main_system_prompt,
        tools=infoscience_tools,
    )

    # Track agent for cleanup
    _active_org_agents.append(agent)

    return agent


async def cleanup_org_agents():
    """Cleanup organization enrichment agents to free memory."""
    global _active_org_agents

    if not _active_org_agents:
        logger.debug("No active organization enrichment agents to cleanup")
        return

    logger.info(f"Cleaning up {len(_active_org_agents)} organization enrichment agents")

    for agent in _active_org_agents.copy():
        try:
            _active_org_agents.remove(agent)
            logger.debug("Organization enrichment agent removed from tracking")
        except Exception as e:
            logger.warning(f"Error during organization enrichment agent cleanup: {e}")

    # Force garbage collection
    import gc

    gc.collect()

    logger.info("Organization enrichment agent cleanup completed")


# Create the primary agent
agent = (
    create_organization_enrichment_agent(org_enrichment_configs[0])
    if org_enrichment_configs
    else None
)

########################################################################
# TOOLS
########################################################################


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


########################################################################
# MAIN ENRICHMENT FUNCTION
########################################################################


async def run_agent_with_retry(
    agent: Agent,
    prompt: str,
    context: OrganizationAnalysisContext,
    config: dict,
) -> Any:
    """
    Run agent with retry logic and exponential backoff.

    Args:
        agent: PydanticAI agent
        prompt: Input prompt
        context: Agent context
        config: Model configuration

    Returns:
        Agent result

    Raises:
        Exception: If all retries fail
    """
    max_retries = config.get("max_retries", 3)
    last_exception = None

    for attempt in range(max_retries):
        try:
            logger.info(
                f"Attempting organization enrichment agent run (attempt {attempt + 1}/{max_retries})",
            )
            result = await agent.run(prompt, deps=context)
            logger.info(
                f"Organization enrichment agent run successful on attempt {attempt + 1}",
            )
            return result
        except Exception as e:
            last_exception = e
            logger.warning(
                f"Organization enrichment agent run failed on attempt {attempt + 1}: {e}",
            )

            if attempt < max_retries - 1:
                delay = get_retry_delay(attempt)
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"All {max_retries} attempts failed")

    raise last_exception or Exception("Organization enrichment agent run failed")


async def run_agent_with_fallback(
    agent_configs: list[dict],
    prompt: str,
    context: OrganizationAnalysisContext,
) -> Any:
    """
    Run agent with fallback to next model if current fails.

    Args:
        agent_configs: List of agent configurations to try
        prompt: Input prompt
        context: Agent context

    Returns:
        Agent result

    Raises:
        Exception: If all models fail
    """
    last_exception = None

    for i, config in enumerate(agent_configs):
        try:
            logger.info(
                f"Trying organization enrichment model {i + 1}/{len(agent_configs)}: {config['provider']}/{config['model']}",
            )
            agent = create_organization_enrichment_agent(config)
            result = await run_agent_with_retry(agent, prompt, context, config)
            logger.info(
                f"Successfully completed organization enrichment with model {i + 1}",
            )
            return result
        except Exception as e:
            last_exception = e
            logger.error(f"Organization enrichment model {i + 1} failed: {e}")
            if i < len(agent_configs) - 1:
                logger.info("Falling back to next organization enrichment model...")
            else:
                logger.error("All organization enrichment models failed")

    raise last_exception or Exception("All organization enrichment models failed")


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
    prompt = get_organization_enrichment_prompt(repository_url, context)

    logger.info(f"🚀 Starting organization enrichment for {repository_url}")
    logger.info(
        f"📊 Input data: {len(context.git_authors)} git authors, {len(context.authors)} ORCID authors",
    )

    # Run the agent with fallback across multiple models
    logger.info("🤖 Running PydanticAI agent with fallback...")
    result = await run_agent_with_fallback(org_enrichment_configs, prompt, context)

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

    # Cleanup agents after successful completion
    await cleanup_org_agents()

    return result.output


from ..data_models import GitAuthor


async def enrich_organizations_from_dict(
    llm_output: Dict[str, Any],
    repository_url: str,
) -> OrganizationEnrichmentResult:
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
    prompt = get_organization_enrichment_prompt(repository_url, context)

    logger.info(f"🚀 Starting organization enrichment from dict for {repository_url}")
    logger.info(
        f"📊 Input data: {len(git_authors)} git authors, {len(authors)} ORCID authors",
    )

    # Run the agent with fallback across multiple models
    logger.info("🤖 Running PydanticAI agent with fallback...")
    result = await run_agent_with_fallback(org_enrichment_configs, prompt, context)

    logger.info(f"✅ Organization enrichment completed for {repository_url}")
    logger.info(
        f"📍 Identified {len(result.output.organizations)} organizations",
    )
    logger.info(
        f"🎯 EPFL relation: {result.output.relatedToEPFL} (confidence: {result.output.relatedToEPFLConfidence:.2f})",
    )

    # Pydantic Validation
    if OrganizationEnrichmentResult.model_validate(result.output):
        logger.info("✅ Output validated against OrganizationEnrichmentResult model")
    else:
        logger.error(
            "✗ Output validation failed against OrganizationEnrichmentResult model",
        )

    return OrganizationEnrichmentResult(**result.output.model_dump())
