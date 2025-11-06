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

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx
from pydantic_ai import Agent, RunContext
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..data_models import Commits, GitAuthor, Person
from ..data_models.user import UserAnalysisContext, UserEnrichmentResult
from ..llm.model_config import (
    create_pydantic_ai_model,
    get_retry_delay,
    load_model_config,
    validate_config,
)
from ..utils.token_counter import estimate_tokens_from_messages
from .user_prompts import (
    get_user_enrichment_agent_prompt,
    user_enrichment_agent_system_prompt,
)

# Configure logging
logger = logging.getLogger(__name__)

# Semaphore to limit concurrent Selenium sessions
# Set to 1 to prevent memory issues (each browser instance uses 500MB-1GB)
# Only increase if using Selenium Grid with multiple nodes AND have sufficient RAM
_MAX_SELENIUM_SESSIONS = int(os.getenv("MAX_SELENIUM_SESSIONS", "1"))
_selenium_semaphore = asyncio.Semaphore(_MAX_SELENIUM_SESSIONS)


# Load model configuration
user_enrichment_configs = load_model_config("run_user_enrichment")

# Validate configurations
for config in user_enrichment_configs:
    if not validate_config(config):
        logger.error(f"Invalid configuration for user enrichment: {config}")
        raise ValueError("Invalid model configuration")

# Agent cleanup tracking
_active_user_agents = []


# Create agent with first configuration
def create_user_enrichment_agent(config: dict) -> Agent:
    """Create a user enrichment agent from configuration."""
    model = create_pydantic_ai_model(config)

    agent = Agent(
        model=model,
        output_type=UserEnrichmentResult,
        system_prompt=user_enrichment_agent_system_prompt,
    )

    # Track agent for cleanup
    _active_user_agents.append(agent)

    return agent


async def cleanup_user_agents():
    """Cleanup user enrichment agents to free memory."""
    global _active_user_agents

    if not _active_user_agents:
        logger.debug("No active user enrichment agents to cleanup")
        return

    logger.info(f"Cleaning up {len(_active_user_agents)} user enrichment agents")

    for agent in _active_user_agents.copy():
        try:
            _active_user_agents.remove(agent)
            logger.debug("User enrichment agent removed from tracking")
        except Exception as e:
            logger.warning(f"Error during user enrichment agent cleanup: {e}")

    # Force garbage collection
    import gc

    gc.collect()

    logger.info("User enrichment agent cleanup completed")


# Create the primary agent
agent = (
    create_user_enrichment_agent(user_enrichment_configs[0])
    if user_enrichment_configs
    else None
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


async def run_agent_with_retry(
    agent: Agent,
    prompt: str,
    context: UserAnalysisContext,
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
                f"Attempting user enrichment agent run (attempt {attempt + 1}/{max_retries})",
            )
            result = await agent.run(prompt, deps=context)
            logger.info(
                f"User enrichment agent run successful on attempt {attempt + 1}",
            )
            return result
        except Exception as e:
            last_exception = e
            logger.warning(
                f"User enrichment agent run failed on attempt {attempt + 1}: {e}",
            )

            if attempt < max_retries - 1:
                delay = get_retry_delay(attempt)
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"All {max_retries} attempts failed")

    raise last_exception or Exception("User enrichment agent run failed")


async def run_agent_with_fallback(
    agent_configs: list[dict],
    prompt: str,
    context: UserAnalysisContext,
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
                f"Trying user enrichment model {i + 1}/{len(agent_configs)}: {config['provider']}/{config['model']}",
            )
            agent = create_user_enrichment_agent(config)
            result = await run_agent_with_retry(agent, prompt, context, config)
            logger.info(f"Successfully completed user enrichment with model {i + 1}")
            return result
        except Exception as e:
            last_exception = e
            logger.error(f"User enrichment model {i + 1} failed: {e}")
            if i < len(agent_configs) - 1:
                logger.info("Falling back to next user enrichment model...")
            else:
                logger.error("All user enrichment models failed")

    raise last_exception or Exception("All user enrichment models failed")


async def enrich_users(
    git_authors: list[GitAuthor],
    existing_authors: list[Person],
    repository_url: str,
) -> dict:
    """
    Enrich user/author information from repository metadata using PydanticAI agent.

    Args:
        git_authors: List of git authors with commit history
        existing_authors: List of existing Person objects (potentially from ORCID)
        repository_url: The repository URL

    Returns:
        Dictionary with 'data' (UserEnrichmentResult) and 'usage' (dict with token info) keys
    """
    # Prepare context for the agent
    context = UserAnalysisContext(
        repository_url=repository_url,
        git_authors=git_authors,
        existing_authors=existing_authors,
    )

    # Prepare the prompt for the agent
    prompt = get_user_enrichment_agent_prompt(repository_url, context)

    logger.info(f"🚀 Starting user enrichment for {repository_url}")
    logger.info(
        f"📊 Input data: {len(context.git_authors)} git authors, {len(context.existing_authors)} existing author records",
    )

    # Run the agent with fallback across multiple models
    logger.info("🤖 Running PydanticAI agent with fallback...")
    result = await run_agent_with_fallback(user_enrichment_configs, prompt, context)

    if result is None:
        logger.error("❌ User enrichment failed - agent returned None")
        return {"data": None, "usage": None}

    # Estimate tokens from prompt and response
    response_text = (
        result.output.model_dump_json()
        if hasattr(result.output, "model_dump_json")
        else ""
    )
    estimated = estimate_tokens_from_messages(
        system_prompt=user_enrichment_agent_system_prompt,
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
            f"User enrichment token usage - Input: {input_tokens}, Output: {output_tokens}",
        )
        logger.info(
            f"User enrichment estimated - Input: {estimated.get('input_tokens', 0)}, Output: {estimated.get('output_tokens', 0)}",
        )
    else:
        logger.warning("Result object has no 'usage' attribute")
        usage_data = {
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_input_tokens": estimated.get("input_tokens", 0),
            "estimated_output_tokens": estimated.get("output_tokens", 0),
        }

    logger.info(f"✅ User enrichment completed for {repository_url}")
    logger.info(
        f"👥 Enriched {len(result.output.enrichedAuthors)} authors",
    )

    # Cleanup agents after successful completion
    await cleanup_user_agents()

    # Log author details
    if result.output.enrichedAuthors:
        logger.info("📋 Enriched authors:")
        for i, author in enumerate(result.output.enrichedAuthors, 1):
            logger.info(
                f"  {i}. {author.name} - {author.currentAffiliation or 'Unknown affiliation'} "
                f"(confidence: {author.confidenceScore:.2f})",
            )

    return {"data": result.output, "usage": usage_data}


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

    # Convert dictionaries to model objects
    git_authors = []
    if git_authors_data is not None:
        for ga_data in git_authors_data:
            # Handle both Pydantic model instances and dictionaries
            if isinstance(ga_data, GitAuthor):
                # Already a GitAuthor instance
                git_authors.append(ga_data)
            elif isinstance(ga_data, dict):
                # Handle Commits object conversion
                commits_data = ga_data.get("commits")
                if commits_data:
                    if isinstance(commits_data, dict):
                        # Parse dates from strings if needed
                        first_date = commits_data.get("firstCommitDate")
                        last_date = commits_data.get("lastCommitDate")

                        if isinstance(first_date, str):
                            first_date = datetime.strptime(
                                first_date,
                                "%Y-%m-%d",
                            ).date()
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
            else:
                logger.warning(f"Unexpected git author data type: {type(ga_data)}")

    # Convert existing authors
    existing_authors = []
    if existing_authors_data is not None:
        for author_data in existing_authors_data:
            # Handle both Pydantic model instances and dictionaries
            if isinstance(author_data, Person):
                # Already a Person instance
                existing_authors.append(author_data)
            elif isinstance(author_data, dict):
                # Handle empty orcid strings (validator handles format conversion)
                author_copy = author_data.copy()
                if "orcid" in author_copy:
                    orcid_value = author_copy["orcid"]
                    if not orcid_value:
                        author_copy["orcid"] = None
                    # Validator will handle format validation and normalization
                existing_authors.append(Person(**author_copy))
            else:
                logger.warning(f"Unexpected author data type: {type(author_data)}")

    # Call the main enrichment function
    result = await enrich_users(git_authors, existing_authors, repository_url)

    # Extract data and usage from result
    if result.get("data") is None:
        return {"enrichedAuthors": [], "usage": None}

    # Return as dictionary with usage info
    enriched_data = result["data"].model_dump()
    enriched_data["usage"] = result.get("usage")
    return enriched_data
