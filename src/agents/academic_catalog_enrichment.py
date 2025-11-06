"""
Academic Catalog Enrichment Agent

This agent searches academic catalogs (Infoscience, OpenAlex, EPFL Graph) to find
related publications, persons, and organizational units.
"""

import logging
from typing import Any

from pydantic_ai import Agent

from ..context.infoscience import (
    get_author_publications_tool,
    search_infoscience_authors_tool,
    search_infoscience_labs_tool,
    search_infoscience_publications_tool,
)
from ..data_models.academic_catalog import AcademicCatalogEnrichmentResult
from ..llm.model_config import (
    create_pydantic_ai_model,
    load_model_config,
    validate_config,
)
from ..utils.token_counter import estimate_tokens_from_messages
from .academic_catalog_prompts import (
    academic_catalog_system_prompt,
    get_organization_academic_catalog_prompt,
    get_repository_academic_catalog_prompt,
    get_user_academic_catalog_prompt,
)

logger = logging.getLogger(__name__)

# Load model configuration for academic catalog enrichment
academic_catalog_configs = load_model_config("run_academic_catalog_enrichment")

# Validate configurations
for config in academic_catalog_configs:
    if not validate_config(config):
        logger.error(
            f"Invalid configuration for academic catalog enrichment: {config}",
        )
        raise ValueError("Invalid model configuration")

# Track active agents for cleanup
_active_catalog_agents = []


def create_academic_catalog_agent(config: dict) -> Agent:
    """Create an academic catalog enrichment agent from configuration."""
    model = create_pydantic_ai_model(config)

    # Define tools for the agent
    tools = [
        search_infoscience_publications_tool,
        search_infoscience_authors_tool,
        search_infoscience_labs_tool,
        get_author_publications_tool,
    ]

    agent = Agent(
        model=model,
        output_type=AcademicCatalogEnrichmentResult,
        system_prompt=academic_catalog_system_prompt,
        tools=tools,
    )

    # Track agent for cleanup
    _active_catalog_agents.append(agent)

    return agent


async def cleanup_catalog_agents():
    """Cleanup academic catalog enrichment agents to free memory."""
    global _active_catalog_agents
    for agent in _active_catalog_agents:
        try:
            # Close/cleanup if the agent has such methods
            if hasattr(agent, "close"):
                await agent.close()
        except Exception as e:
            logger.warning(f"Error cleaning up catalog agent: {e}")
    _active_catalog_agents = []


async def run_agent_with_fallback(
    agent_configs: list[dict],
    prompt: str,
) -> Any:
    """
    Run the academic catalog enrichment agent with fallback across multiple models.

    Args:
        agent_configs: List of model configurations to try
        prompt: The enrichment prompt

    Returns:
        Result dict with 'data' and 'usage' keys
    """
    last_exception = None

    for idx, config in enumerate(agent_configs):
        try:
            logger.info(
                f"Attempting academic catalog enrichment with model {idx + 1}/{len(agent_configs)}: {config.get('model')}",
            )

            # Create agent
            agent = create_academic_catalog_agent(config)

            # Run the agent
            result = await agent.run(prompt)

            # Extract output and usage
            output = result.output

            # Extract token usage
            usage_data = {}
            if hasattr(result, "usage"):
                usage = result.usage
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0

                # Fallback to details field for certain models
                if (
                    input_tokens == 0
                    and output_tokens == 0
                    and hasattr(usage, "details")
                ):
                    details = usage.details
                    if isinstance(details, dict):
                        input_tokens = details.get("input_tokens", 0)
                        output_tokens = details.get("output_tokens", 0)

                usage_data = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }

                logger.info(
                    f"✓ Academic catalog enrichment succeeded with {input_tokens} input, {output_tokens} output tokens",
                )

            # Estimate tokens as fallback
            response_text = (
                output.model_dump_json() if hasattr(output, "model_dump_json") else ""
            )
            estimated = estimate_tokens_from_messages(
                system_prompt=academic_catalog_system_prompt,
                user_prompt=prompt,
                response=response_text,
            )

            usage_data["estimated_input_tokens"] = estimated.get("input_tokens", 0)
            usage_data["estimated_output_tokens"] = estimated.get("output_tokens", 0)

            return {"data": output, "usage": usage_data}

        except Exception as e:
            logger.warning(
                f"Academic catalog enrichment failed with model {config.get('model')}: {e}",
            )
            last_exception = e
            continue

    logger.error(
        f"All academic catalog enrichment models failed. Last error: {last_exception}",
    )
    raise (last_exception or Exception("All academic catalog enrichment models failed"))


async def enrich_repository_academic_catalog(
    repository_url: str,
    repository_name: str,
    description: str,
    readme_excerpt: str,
    authors: list = None,
    organizations: list = None,
) -> dict:
    """
    Enrich repository with academic catalog relations.

    Args:
        repository_url: URL of the repository
        repository_name: Name of the repository
        description: Repository description
        readme_excerpt: Excerpt from README
        authors: List of identified author names
        organizations: List of identified organization names

    Returns:
        Dictionary with 'data' (AcademicCatalogEnrichmentResult) and 'usage' keys
    """
    prompt = get_repository_academic_catalog_prompt(
        repository_url=repository_url,
        repository_name=repository_name,
        description=description,
        readme_excerpt=readme_excerpt,
        authors=authors or [],
        organizations=organizations or [],
    )

    logger.info(
        f"🔍 Starting academic catalog enrichment for repository: {repository_name}",
    )

    try:
        result = await run_agent_with_fallback(academic_catalog_configs, prompt)

        if result and result.get("data"):
            enrichment_data = result["data"]
            logger.info(
                f"✓ Found {len(enrichment_data.relations)} academic catalog relations",
            )

        return result
    except Exception as e:
        logger.error(f"Academic catalog enrichment failed: {e}")
        # Return empty result instead of failing
        return {
            "data": AcademicCatalogEnrichmentResult(
                relations=[],
                searchStrategy="Enrichment failed",
                totalSearches=0,
            ),
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


async def enrich_user_academic_catalog(
    username: str,
    full_name: str,
    bio: str,
    organizations: list,
) -> dict:
    """
    Enrich user with academic catalog relations.

    Args:
        username: GitHub username
        full_name: User's full name
        bio: User's bio
        organizations: List of organizations

    Returns:
        Dictionary with 'data' (AcademicCatalogEnrichmentResult) and 'usage' keys
    """
    prompt = get_user_academic_catalog_prompt(
        username=username,
        full_name=full_name,
        bio=bio,
        organizations=organizations,
    )

    logger.info(f"🔍 Starting academic catalog enrichment for user: {username}")

    try:
        result = await run_agent_with_fallback(academic_catalog_configs, prompt)

        if result and result.get("data"):
            enrichment_data = result["data"]
            logger.info(
                f"✓ Found {len(enrichment_data.relations)} academic catalog relations",
            )

        return result
    except Exception as e:
        logger.error(f"Academic catalog enrichment failed: {e}")
        # Return empty result instead of failing
        return {
            "data": AcademicCatalogEnrichmentResult(
                relations=[],
                searchStrategy="Enrichment failed",
                totalSearches=0,
            ),
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


async def enrich_organization_academic_catalog(
    org_name: str,
    description: str,
    website: str,
    members: list,
) -> dict:
    """
    Enrich organization with academic catalog relations.

    Args:
        org_name: Organization name
        description: Organization description
        website: Organization website
        members: List of member usernames

    Returns:
        Dictionary with 'data' (AcademicCatalogEnrichmentResult) and 'usage' keys
    """
    prompt = get_organization_academic_catalog_prompt(
        org_name=org_name,
        description=description,
        website=website,
        members=members,
    )

    logger.info(f"🔍 Starting academic catalog enrichment for organization: {org_name}")

    try:
        result = await run_agent_with_fallback(academic_catalog_configs, prompt)

        if result and result.get("data"):
            enrichment_data = result["data"]
            logger.info(
                f"✓ Found {len(enrichment_data.relations)} academic catalog relations",
            )

        return result
    except Exception as e:
        logger.error(f"Academic catalog enrichment failed: {e}")
        # Return empty result instead of failing
        return {
            "data": AcademicCatalogEnrichmentResult(
                relations=[],
                searchStrategy="Enrichment failed",
                totalSearches=0,
            ),
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
