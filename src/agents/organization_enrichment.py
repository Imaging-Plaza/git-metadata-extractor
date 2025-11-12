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
from .url_validation import validate_ror_url
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
from ..utils.token_counter import estimate_tokens_from_messages
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


# Define validation tool function (must be defined before agent creation)
async def validate_ror_organization_tool(
    ctx: RunContext[OrganizationAnalysisContext],
    ror_id: str,
    org_name: str,
    org_data: dict,
) -> str:
    """
    Validate that a ROR ID points to the correct organization by fetching HTML and checking.

    Args:
        ctx: The run context
        ror_id: ROR ID (full URL or just ID)
        org_name: Expected organization name
        org_data: Dictionary with expected organization data (country, type, website, etc.)

    Returns:
        JSON string with validation result
    """
    logger.info(f"🔍 Agent tool called: validate_ror_organization('{ror_id}', '{org_name}')")
    try:
        # Prepare expected org dict
        expected_org = {
            "name": org_name,
            "country": org_data.get("country"),
            "type": org_data.get("type"),
            "website": org_data.get("website"),
            "aliases": org_data.get("aliases", []),
        }

        # Validate using agent delegation
        validation_result = await validate_ror_url(
            ror_id=ror_id,
            expected_org=expected_org,
            ctx=ctx,
        )

        # Return as JSON string
        return json.dumps(
            {
                "is_valid": validation_result.is_valid,
                "confidence": validation_result.confidence,
                "justification": validation_result.justification,
                "matched_fields": validation_result.matched_fields,
                "validation_errors": validation_result.validation_errors,
            },
            indent=2,
        )

    except Exception as e:
        logger.error(f"✗ Error validating ROR organization: {e}", exc_info=True)
        return json.dumps(
            {
                "is_valid": False,
                "confidence": 0.0,
                "justification": f"Error during validation: {str(e)}",
                "matched_fields": [],
                "validation_errors": [str(e)],
            },
            indent=2,
        )


# Create agent with first configuration
def create_organization_enrichment_agent(config: dict) -> Agent:
    """Create an organization enrichment agent from configuration."""
    model = create_pydantic_ai_model(config)

    # Define Infoscience tools for the organization agent
    infoscience_tools = [
        search_infoscience_labs_tool,
        search_infoscience_publications_tool,
        get_author_publications_tool,
        validate_ror_organization_tool,  # Add validation tool
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
                # Extract name from names array if name field is null
                # Prefer ror_display or label type names
                org_name = item.get("name")
                if not org_name and item.get("names"):
                    for name_entry in item.get("names", []):
                        if "ror_display" in name_entry.get("types", []):
                            org_name = name_entry.get("value")
                            break
                    # If no ror_display, use first label
                    if not org_name:
                        for name_entry in item.get("names", []):
                            if "label" in name_entry.get("types", []):
                                org_name = name_entry.get("value")
                                break
                    # Fallback to first name value
                    if not org_name and item.get("names"):
                        org_name = item.get("names", [{}])[0].get("value")
                
                # Extract country from locations if country field is not available
                country = item.get("country", {}).get("country_name")
                if not country and item.get("locations"):
                    country = item.get("locations", [{}])[0].get("geonames_details", {}).get("country_name")
                
                org_info = {
                    "name": org_name,
                    "ror_id": item.get("id"),
                    "types": item.get("types", []),
                    "country": country,
                    "aliases": item.get("aliases", []),
                    "acronyms": item.get("acronyms", []),
                    "links": item.get("links", []),
                    "names": item.get("names", []),  # Include full names array for agent
                    "locations": item.get("locations", []),  # Include locations for country info
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
            # Log the actual results for debugging (INFO level so agent can see what it has)
            logger.info(f"📋 ROR search results for '{query}':")
            for i, result in enumerate(results[:5], 1):  # Show top 5
                ror_id_clean = result.get("ror_id", "").split("/")[-1] if "/" in result.get("ror_id", "") else result.get("ror_id", "")
                logger.info(
                    f"  {i}. {result.get('name', 'N/A')} - ROR ID: {ror_id_clean}"
                )
            
            # DEBUG: Show EXACTLY what we're returning to the agent
            json_result = json.dumps(results, indent=2)
            logger.info(f"🔍 DEBUG - EXACT ROR SEARCH RESULT PROVIDED TO AGENT for '{query}':")
            logger.info(f"JSON returned to agent ({len(json_result)} chars):")
            logger.info(json_result)
            logger.debug(f"Full ROR search results for '{query}': {json.dumps(results, indent=2)}")
            return json_result

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


async def _pre_search_ror_for_organizations(
    context: OrganizationAnalysisContext,
) -> Dict[str, Any]:
    """
    Proactively search ROR for organizations identified from ORCID affiliations and existing mentions.
    Does NOT search for email domains - let the agent decide on those.
    
    Returns:
        Dictionary mapping organization names/queries to their ROR search results
    """
    ror_results = {}
    organizations_to_search = set()
    
    # Extract from ORCID affiliations
    for author in context.authors:
        if author.affiliations:
            for aff in author.affiliations:
                if aff and aff.strip():
                    organizations_to_search.add(aff.strip())
    
    # Add existing organization mentions
    for org in context.existing_organizations:
        if org and org.strip():
            organizations_to_search.add(org.strip())
    
    logger.info(f"🔍 Pre-searching ROR for {len(organizations_to_search)} organizations (from ORCID and existing mentions)...")
    
    # Search ROR for each organization
    for org_query in organizations_to_search:
        try:
            logger.info(f"  Pre-searching ROR for: '{org_query}'")
            # Use the same logic as search_ror tool
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.ror.org/organizations",
                    params={"query": org_query},
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()
                
                # Process results (same logic as search_ror tool)
                results = []
                for item in data.get("items", [])[:5]:
                    # Extract name from names array
                    org_name = item.get("name")
                    if not org_name and item.get("names"):
                        for name_entry in item.get("names", []):
                            if "ror_display" in name_entry.get("types", []):
                                org_name = name_entry.get("value")
                                break
                        if not org_name:
                            for name_entry in item.get("names", []):
                                if "label" in name_entry.get("types", []):
                                    org_name = name_entry.get("value")
                                    break
                        if not org_name and item.get("names"):
                            org_name = item.get("names", [{}])[0].get("value")
                    
                    # Extract country from locations if country field is not available
                    country = item.get("country", {}).get("country_name")
                    if not country and item.get("locations"):
                        country = item.get("locations", [{}])[0].get("geonames_details", {}).get("country_name")
                    
                    # Extract only essential fields for affiliation matching
                    # Limit to parent relationships only (most relevant for affiliation)
                    parent_relationships = [
                        {
                            "label": rel.get("label"),
                            "type": rel.get("type"),
                            "id": rel.get("id"),
                        }
                        for rel in item.get("relationships", [])
                        if rel.get("type") == "parent"
                    ][:2]  # Limit to 2 parent relationships max
                    
                    # Extract website from links
                    website = None
                    if item.get("links"):
                        for link in item.get("links", []):
                            if link.get("type") == "website":
                                website = link.get("value")
                                break
                    
                    # Extract key aliases (limit to 3 most important)
                    key_aliases = []
                    if item.get("names"):
                        for name_entry in item.get("names", []):
                            if name_entry.get("value") != org_name:
                                alias = name_entry.get("value")
                                if alias and alias not in key_aliases:
                                    key_aliases.append(alias)
                                    if len(key_aliases) >= 3:
                                        break
                    
                    org_info = {
                        "name": org_name,
                        "ror_id": item.get("id"),
                        "country": country,
                        "website": website,
                        "aliases": key_aliases[:3],  # Max 3 aliases
                        "parent_organizations": parent_relationships,  # Only parent relationships
                    }
                    results.append(org_info)
                
                if results:
                    ror_results[org_query] = results
                    logger.info(f"  ✓ Found {len(results)} ROR results for '{org_query}'")
                else:
                    logger.info(f"  ⚠ No ROR results for '{org_query}'")
                    
        except Exception as e:
            logger.warning(f"  ✗ Error pre-searching ROR for '{org_query}': {e}")
    
    logger.info(f"✅ Pre-searched ROR for {len(ror_results)} organizations")
    return ror_results


async def enrich_organizations(
    repository_metadata: SoftwareSourceCode,
    repository_url: str,
) -> dict:
    """
    Enrich organization information from repository metadata using PydanticAI agent.

    Args:
        repository_metadata: The initial LLM analysis result
        repository_url: The repository URL

    Returns:
        Dictionary with 'data' (OrganizationEnrichmentResult) and 'usage' (dict with token info) keys
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

    # Pre-search ROR for organizations from ORCID and existing mentions
    logger.info("🔍 Pre-searching ROR for organizations from ORCID affiliations and existing mentions...")
    pre_searched_ror = await _pre_search_ror_for_organizations(context)
    
    # DEBUG: Log what we found
    if pre_searched_ror:
        logger.info(f"📋 Pre-searched ROR results: {len(pre_searched_ror)} organizations")
        for org_query, results in pre_searched_ror.items():
            logger.info(f"  '{org_query}': {len(results)} ROR matches")
            for i, result in enumerate(results[:3], 1):
                ror_id = result.get("ror_id", "").split("/")[-1] if "/" in result.get("ror_id", "") else result.get("ror_id", "")
                logger.info(f"    {i}. {result.get('name')} - ROR: {ror_id}")
    else:
        logger.info("  No organizations found to pre-search")
    
    # Prepare the prompt for the agent (include pre-searched ROR results)
    prompt = get_organization_enrichment_prompt(repository_url, context, pre_searched_ror)

    logger.info(f"🚀 Starting organization enrichment for {repository_url}")
    logger.info(
        f"📊 Input data: {len(context.git_authors)} git authors, {len(context.authors)} ORCID authors",
    )
    
    # Run the agent with fallback across multiple models
    logger.info("🤖 Running PydanticAI agent with fallback...")
    result = await run_agent_with_fallback(org_enrichment_configs, prompt, context)

    # Estimate tokens from prompt and response
    response_text = (
        result.output.model_dump_json()
        if hasattr(result.output, "model_dump_json")
        else ""
    )
    estimated = estimate_tokens_from_messages(
        system_prompt=organization_enrichment_main_system_prompt,
        user_prompt=prompt,
        response=response_text,
    )

    # Extract usage information from the result
    usage_data = None
    if hasattr(result, "usage"):
        usage = result.usage

        # First try to get tokens from direct attributes
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0

        # If tokens are 0, check the details field (for Anthropic, OpenAI reasoning models, etc.)
        # See: https://github.com/pydantic/pydantic-ai/issues/3223
        if input_tokens == 0 and output_tokens == 0 and hasattr(usage, "details"):
            details = usage.details
            if isinstance(details, dict):
                input_tokens = details.get("input_tokens", 0) or 0
                output_tokens = details.get("output_tokens", 0) or 0
                logger.debug(
                    f"Extracted tokens from usage.details: input={input_tokens}, output={output_tokens}",
                )

        usage_data = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_input_tokens": estimated.get("input_tokens", 0),
            "estimated_output_tokens": estimated.get("output_tokens", 0),
        }
        logger.info(
            f"Organization enrichment token usage - Input: {input_tokens}, Output: {output_tokens}",
        )
        logger.info(
            f"Organization enrichment estimated - Input: {estimated.get('input_tokens', 0)}, Output: {estimated.get('output_tokens', 0)}",
        )
    else:
        logger.warning("Result object has no 'usage' attribute")
        usage_data = {
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_input_tokens": estimated.get("input_tokens", 0),
            "estimated_output_tokens": estimated.get("output_tokens", 0),
        }

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
            ror_id = str(org.hasRorId) if org.hasRorId else "None"
            logger.info(f"  {i}. {org_name} - ROR ID: {ror_id}")
    
    # Validate ROR IDs for all organizations
    logger.info("🔍 Validating ROR IDs for all organizations...")
    for org in result.output.organizations:
        if org.hasRorId:
            try:
                # Extract ROR ID from URL if needed
                ror_id = str(org.hasRorId)
                if ror_id.startswith("http://") or ror_id.startswith("https://"):
                    ror_id = ror_id.split("/")[-1]

                logger.info(
                    f"🔍 Validating ROR ID for {org.legalName}: {ror_id} (URL: https://ror.org/{ror_id})"
                )

                # Quick pre-validation: Check if ROR ID exists in ROR API
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        ror_api_url = f"https://api.ror.org/organizations/{ror_id}"
                        api_response = await client.get(ror_api_url)
                        if api_response.status_code == 404:
                            logger.warning(
                                f"⚠ ROR ID {ror_id} does not exist in ROR API (404). "
                                f"Removing invalid ROR ID for {org.legalName}"
                            )
                            org.hasRorId = None
                            continue
                        elif api_response.status_code != 200:
                            logger.warning(
                                f"⚠ ROR API returned {api_response.status_code} for {ror_id}. "
                                f"Proceeding with full validation..."
                            )
                except Exception as e:
                    logger.debug(
                        f"Pre-validation check failed for {ror_id}: {e}. Proceeding with full validation..."
                    )

                # Prepare expected org data
                expected_org = {
                    "name": org.legalName or "",
                    "country": org.country,
                    "type": org.organizationType,
                    "website": str(org.website) if org.website else None,
                    "aliases": org.alternateNames or [],
                }

                # Validate ROR URL (no context needed for post-processing)
                validation_result = await validate_ror_url(
                    ror_id=ror_id,
                    expected_org=expected_org,
                    ctx=None,
                )

                if not validation_result.is_valid:
                    logger.warning(
                        f"⚠ ROR validation failed for {org.legalName} (ROR: {ror_id}): "
                        f"{validation_result.justification}"
                    )
                    # Remove invalid ROR ID
                    org.hasRorId = None
                elif validation_result.confidence < 0.7:
                    logger.info(
                        f"⚠ Low confidence ROR match for {org.legalName} (ROR: {ror_id}): "
                        f"confidence={validation_result.confidence:.2f}"
                    )
                    # Reduce confidence
                    if org.attributionConfidence:
                        org.attributionConfidence *= 0.7
                else:
                    logger.info(
                        f"✓ ROR validation passed for {org.legalName} (ROR: {ror_id}): "
                        f"confidence={validation_result.confidence:.2f}"
                    )

            except Exception as e:
                logger.error(
                    f"Error validating ROR ID for {org.legalName}: {e}",
                    exc_info=True,
                )
                # Remove ROR ID on error
                org.hasRorId = None

    logger.info("✅ ROR validation completed")

    # Cleanup agents after successful completion
    await cleanup_org_agents()

    return {"data": result.output, "usage": usage_data}


from ..data_models import GitAuthor


async def enrich_organizations_from_dict(
    llm_output: Dict[str, Any],
    repository_url: str,
) -> dict:
    """
    Convenience function to enrich organizations from a dictionary (e.g., from API response).

    Args:
        llm_output: The initial LLM analysis output as a dictionary
        repository_url: The repository URL

    Returns:
        Dictionary with enriched organization information and usage data
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
                # Check if it's a Person (has name, orcid, or affiliation)
                if "name" in author or "orcid" in author or "affiliation" in author:
                    # Handle empty orcid strings
                    author_data = author.copy()
                    if "orcid" in author_data and not author_data["orcid"]:
                        author_data["orcid"] = None
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

    # Pre-search ROR for organizations from ORCID and existing mentions
    logger.info("🔍 Pre-searching ROR for organizations from ORCID affiliations and existing mentions...")
    pre_searched_ror = await _pre_search_ror_for_organizations(context)
    
    # DEBUG: Log what we found
    if pre_searched_ror:
        logger.info(f"📋 Pre-searched ROR results: {len(pre_searched_ror)} organizations")
        for org_query, results in pre_searched_ror.items():
            logger.info(f"  '{org_query}': {len(results)} ROR matches")
            for i, result in enumerate(results[:3], 1):
                ror_id = result.get("ror_id", "").split("/")[-1] if "/" in result.get("ror_id", "") else result.get("ror_id", "")
                logger.info(f"    {i}. {result.get('name')} - ROR: {ror_id}")
    else:
        logger.info("  No organizations found to pre-search")
    
    # Prepare the prompt for the agent (include pre-searched ROR results)
    prompt = get_organization_enrichment_prompt(repository_url, context, pre_searched_ror)

    logger.info(f"🚀 Starting organization enrichment from dict for {repository_url}")
    logger.info(
        f"📊 Input data: {len(git_authors)} git authors, {len(authors)} ORCID authors",
    )
    
    # Run the agent with fallback across multiple models
    logger.info("🤖 Running PydanticAI agent with fallback...")
    result = await run_agent_with_fallback(org_enrichment_configs, prompt, context)

    # Estimate tokens from prompt and response
    response_text = (
        result.output.model_dump_json()
        if hasattr(result.output, "model_dump_json")
        else ""
    )
    estimated = estimate_tokens_from_messages(
        system_prompt=organization_enrichment_main_system_prompt,
        user_prompt=prompt,
        response=response_text,
    )

    # Extract usage information from the result (before accessing result.output)
    usage_data = None
    if hasattr(result, "usage"):
        usage = result.usage

        # First try to get tokens from direct attributes
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0

        # If tokens are 0, check the details field (for Anthropic, OpenAI reasoning models, etc.)
        # See: https://github.com/pydantic/pydantic-ai/issues/3223
        if input_tokens == 0 and output_tokens == 0 and hasattr(usage, "details"):
            details = usage.details
            if isinstance(details, dict):
                input_tokens = details.get("input_tokens", 0) or 0
                output_tokens = details.get("output_tokens", 0) or 0
                logger.debug(
                    f"Extracted tokens from usage.details: input={input_tokens}, output={output_tokens}",
                )

        usage_data = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_input_tokens": estimated.get("input_tokens", 0),
            "estimated_output_tokens": estimated.get("output_tokens", 0),
        }
        logger.info(
            f"Organization enrichment (from_dict) token usage - Input: {input_tokens}, Output: {output_tokens}",
        )
        logger.info(
            f"Organization enrichment (from_dict) estimated - Input: {estimated.get('input_tokens', 0)}, Output: {estimated.get('output_tokens', 0)}",
        )
    else:
        logger.warning("Result object has no 'usage' attribute")
        usage_data = {
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_input_tokens": estimated.get("input_tokens", 0),
            "estimated_output_tokens": estimated.get("output_tokens", 0),
        }

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
            ror_id = str(org.hasRorId) if org.hasRorId else "None"
            logger.info(f"  {i}. {org_name} - ROR ID: {ror_id}")
    
    # Pydantic Validation
    if OrganizationEnrichmentResult.model_validate(result.output):
        logger.info("✅ Output validated against OrganizationEnrichmentResult model")
    else:
        logger.error(
            "✗ Output validation failed against OrganizationEnrichmentResult model",
        )

    # Validate ROR IDs for all organizations
    enriched_result = OrganizationEnrichmentResult(**result.output.model_dump())
    
    logger.info("🔍 Validating ROR IDs for all organizations...")
    for org in enriched_result.organizations:
        if org.hasRorId:
            try:
                # Extract ROR ID from URL if needed
                ror_id = str(org.hasRorId)
                if ror_id.startswith("http://") or ror_id.startswith("https://"):
                    ror_id = ror_id.split("/")[-1]

                logger.info(
                    f"🔍 Validating ROR ID for {org.legalName}: {ror_id} (URL: https://ror.org/{ror_id})"
                )

                # Quick pre-validation: Check if ROR ID exists in ROR API
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        ror_api_url = f"https://api.ror.org/organizations/{ror_id}"
                        api_response = await client.get(ror_api_url)
                        if api_response.status_code == 404:
                            logger.warning(
                                f"⚠ ROR ID {ror_id} does not exist in ROR API (404). "
                                f"Removing invalid ROR ID for {org.legalName}"
                            )
                            org.hasRorId = None
                            continue
                        elif api_response.status_code != 200:
                            logger.warning(
                                f"⚠ ROR API returned {api_response.status_code} for {ror_id}. "
                                f"Proceeding with full validation..."
                            )
                except Exception as e:
                    logger.debug(
                        f"Pre-validation check failed for {ror_id}: {e}. Proceeding with full validation..."
                    )

                # Prepare expected org data
                expected_org = {
                    "name": org.legalName or "",
                    "country": org.country,
                    "type": org.organizationType,
                    "website": str(org.website) if org.website else None,
                    "aliases": org.alternateNames or [],
                }

                # Validate ROR URL (no context needed for post-processing)
                validation_result = await validate_ror_url(
                    ror_id=ror_id,
                    expected_org=expected_org,
                    ctx=None,
                )

                if not validation_result.is_valid:
                    logger.warning(
                        f"⚠ ROR validation failed for {org.legalName} (ROR: {ror_id}): "
                        f"{validation_result.justification}"
                    )
                    # Remove invalid ROR ID
                    org.hasRorId = None
                elif validation_result.confidence < 0.7:
                    logger.info(
                        f"⚠ Low confidence ROR match for {org.legalName} (ROR: {ror_id}): "
                        f"confidence={validation_result.confidence:.2f}"
                    )
                    # Reduce confidence
                    if org.attributionConfidence:
                        org.attributionConfidence *= 0.7
                else:
                    logger.info(
                        f"✓ ROR validation passed for {org.legalName} (ROR: {ror_id}): "
                        f"confidence={validation_result.confidence:.2f}"
                    )

            except Exception as e:
                logger.error(
                    f"Error validating ROR ID for {org.legalName}: {e}",
                    exc_info=True,
                )
                # Remove ROR ID on error
                org.hasRorId = None

    logger.info("✅ ROR validation completed")

    return {
        "data": enriched_result,
        "usage": usage_data,
    }
