"""
linked entities Enrichment Agent

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
from ..data_models.linked_entities import (
    CatalogType,
    EntityType,
    linkedEntitiesEnrichmentResult,
    linkedEntitiesRelation,
)
from ..llm.model_config import (
    create_pydantic_ai_model,
    load_model_config,
    validate_config,
)
from ..utils.token_counter import estimate_tokens_from_messages
from .linked_entities_prompts import (
    get_organization_linked_entities_prompt,
    get_repository_linked_entities_prompt,
    get_user_linked_entities_prompt,
    linked_entities_system_prompt,
)
from .url_validation import validate_infoscience_url

logger = logging.getLogger(__name__)

# Load model configuration for linked entities enrichment
linked_entities_configs = load_model_config("run_linked_entities_enrichment")

# Validate configurations
for config in linked_entities_configs:
    if not validate_config(config):
        logger.error(
            f"Invalid configuration for linked entities enrichment: {config}",
        )
        raise ValueError("Invalid model configuration")

# Track active agents for cleanup
_active_linked_entities_agents = []


def create_linked_entities_agent(config: dict) -> Agent:
    """Create an linked entities enrichment agent from configuration."""
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
        output_type=linkedEntitiesEnrichmentResult,
        system_prompt=linked_entities_system_prompt,
        tools=tools,
    )

    # Track agent for cleanup
    _active_linked_entities_agents.append(agent)

    return agent


async def cleanup_linked_entities_agents():
    """Cleanup linked entities enrichment agents to free memory."""
    global _active_linked_entities_agents
    for agent in _active_linked_entities_agents:
        try:
            # Close/cleanup if the agent has such methods
            if hasattr(agent, "close"):
                await agent.close()
        except Exception as e:
            logger.warning(f"Error cleaning up catalog agent: {e}")
    _active_linked_entities_agents = []


async def run_agent_with_fallback(
    agent_configs: list[dict],
    prompt: str,
) -> Any:
    """
    Run the linked entities enrichment agent with fallback across multiple models.

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
                f"Attempting linked entities enrichment with model {idx + 1}/{len(agent_configs)}: {config.get('model')}",
            )

            # Create agent
            agent = create_linked_entities_agent(config)

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
                    f"✓ linked entities enrichment succeeded with {input_tokens} input, {output_tokens} output tokens",
                )

            # Estimate tokens as fallback
            response_text = (
                output.model_dump_json() if hasattr(output, "model_dump_json") else ""
            )
            estimated = estimate_tokens_from_messages(
                system_prompt=linked_entities_system_prompt,
                user_prompt=prompt,
                response=response_text,
            )

            usage_data["estimated_input_tokens"] = estimated.get("input_tokens", 0)
            usage_data["estimated_output_tokens"] = estimated.get("output_tokens", 0)

            return {"data": output, "usage": usage_data}

        except Exception as e:
            logger.warning(
                f"linked entities enrichment failed with model {config.get('model')}: {e}",
            )
            last_exception = e
            continue

    logger.error(
        f"All linked entities enrichment models failed. Last error: {last_exception}",
    )
    raise (last_exception or Exception("All linked entities enrichment models failed"))


async def _validate_infoscience_relations(
    relations: list[linkedEntitiesRelation],
) -> list[linkedEntitiesRelation]:
    """
    Validate and normalize Infoscience URLs in linked entities relations.

    Args:
        relations: List of linkedEntitiesRelation objects

    Returns:
        Filtered list with validated relations (invalid ones removed)
    """
    validated_relations = []

    for relation in relations:
        # Only validate Infoscience relations
        if relation.catalogType != CatalogType.INFOSCIENCE:
            validated_relations.append(relation)
            continue

        # Get entity URL
        entity_url = None
        if hasattr(relation.entity, "url"):
            entity_url = relation.entity.url
        elif hasattr(relation.entity, "profile_url"):
            entity_url = relation.entity.profile_url
        elif isinstance(relation.entity, dict):
            entity_url = relation.entity.get("url") or relation.entity.get(
                "profile_url",
            )

        if not entity_url:
            logger.warning(
                f"Skipping relation without URL: {relation.get_display_name()}",
            )
            continue

        # Extract UUID from entity (handles both Pydantic models and dicts)
        entity_uuid = None
        if hasattr(relation.entity, "uuid"):
            entity_uuid = relation.entity.uuid
        elif isinstance(relation.entity, dict):
            entity_uuid = relation.entity.get("uuid")

        # For Infoscience, UUID is mandatory
        if not entity_uuid:
            logger.warning(
                f"Skipping Infoscience relation without UUID: {relation.get_display_name()}",
            )
            continue

        try:
            # Prepare expected entity data based on entity type
            expected_entity = {}
            if relation.entityType == EntityType.PUBLICATION:
                if hasattr(relation.entity, "title"):
                    expected_entity["title"] = relation.entity.title
                    expected_entity["authors"] = getattr(relation.entity, "authors", [])
                    expected_entity["doi"] = getattr(relation.entity, "doi", None)
                    expected_entity["publication_date"] = getattr(
                        relation.entity,
                        "publication_date",
                        None,
                    )
                    expected_entity["lab"] = getattr(relation.entity, "lab", None)
                elif isinstance(relation.entity, dict):
                    expected_entity = relation.entity
            elif relation.entityType == EntityType.PERSON:
                if hasattr(relation.entity, "name"):
                    expected_entity["name"] = relation.entity.name
                    expected_entity["affiliation"] = getattr(
                        relation.entity,
                        "affiliation",
                        None,
                    )
                    expected_entity["orcid"] = getattr(relation.entity, "orcid", None)
                    expected_entity["email"] = getattr(relation.entity, "email", None)
                elif isinstance(relation.entity, dict):
                    expected_entity = relation.entity
            elif relation.entityType == EntityType.ORGUNIT:
                if hasattr(relation.entity, "name"):
                    expected_entity["name"] = relation.entity.name
                    expected_entity["parent_organization"] = getattr(
                        relation.entity,
                        "parent_organization",
                        None,
                    )
                    expected_entity["description"] = getattr(
                        relation.entity,
                        "description",
                        None,
                    )
                elif isinstance(relation.entity, dict):
                    expected_entity = relation.entity

            # Validate Infoscience URL
            validation_result = await validate_infoscience_url(
                url=str(entity_url),
                expected_entity=expected_entity,
                entity_type=relation.entityType.value,
                ctx=None,
            )

            if not validation_result.is_valid:
                logger.warning(
                    f"⚠ Infoscience validation failed for {relation.get_display_name()}: "
                    f"{validation_result.justification}",
                )
                # Skip invalid relation
                continue

            # Update URL if normalized
            if (
                validation_result.normalized_url
                and validation_result.normalized_url != entity_url
            ):
                logger.info(
                    f"✓ Normalized Infoscience URL: {entity_url} -> {validation_result.normalized_url}",
                )
                # Update entity URL
                if hasattr(relation.entity, "url"):
                    relation.entity.url = validation_result.normalized_url
                elif hasattr(relation.entity, "profile_url"):
                    relation.entity.profile_url = validation_result.normalized_url
                elif isinstance(relation.entity, dict):
                    relation.entity["url"] = validation_result.normalized_url

            # Update confidence based on validation
            if validation_result.confidence < 0.6:
                logger.info(
                    f"⚠ Low confidence Infoscience match for {relation.get_display_name()}: "
                    f"confidence={validation_result.confidence:.2f}",
                )
                # Reduce relation confidence
                relation.confidence = min(
                    relation.confidence,
                    validation_result.confidence,
                )
            else:
                logger.info(
                    f"✓ Infoscience validation passed for {relation.get_display_name()}: "
                    f"confidence={validation_result.confidence:.2f}",
                )

            validated_relations.append(relation)

        except Exception as e:
            logger.error(
                f"Error validating Infoscience URL for {relation.get_display_name()}: {e}",
                exc_info=True,
            )
            # Skip relation on error
            continue

    return validated_relations


async def enrich_repository_linked_entities(
    repository_url: str,
    repository_name: str,
    description: str,
    readme_excerpt: str,
    authors: list = None,
    organizations: list = None,
) -> dict:
    """
    Enrich repository with linked entities relations.

    Args:
        repository_url: URL of the repository
        repository_name: Name of the repository
        description: Repository description
        readme_excerpt: Excerpt from README
        authors: List of identified author names
        organizations: List of identified organization names

    Returns:
        Dictionary with 'data' (linkedEntitiesEnrichmentResult) and 'usage' keys
    """
    prompt = get_repository_linked_entities_prompt(
        repository_url=repository_url,
        repository_name=repository_name,
        description=description,
        readme_excerpt=readme_excerpt,
        authors=authors or [],
        organizations=organizations or [],
    )

    logger.info(
        f"🔍 Starting linked entities enrichment for repository: {repository_name}",
    )

    try:
        result = await run_agent_with_fallback(linked_entities_configs, prompt)

        if result and result.get("data"):
            enrichment_data = result["data"]

            # Validate Infoscience URLs in repository relations
            logger.info("🔍 Validating Infoscience URLs in repository relations...")
            enrichment_data.repository_relations = (
                await _validate_infoscience_relations(
                    enrichment_data.repository_relations,
                )
            )

            logger.info(
                f"✓ Found {len(enrichment_data.repository_relations)} validated repository relations",
            )

            # Validate Infoscience URLs in author relations
            if hasattr(enrichment_data, "author_relations"):
                logger.info("🔍 Validating Infoscience URLs in author relations...")
                for author_name, relations in enrichment_data.author_relations.items():
                    enrichment_data.author_relations[
                        author_name
                    ] = await _validate_infoscience_relations(
                        relations,
                    )
                logger.info(
                    f"✓ Validated author relations for {len(enrichment_data.author_relations)} authors",
                )

            # Validate Infoscience URLs in organization relations
            if hasattr(enrichment_data, "organization_relations"):
                logger.info(
                    "🔍 Validating Infoscience URLs in organization relations...",
                )
                for (
                    org_name,
                    relations,
                ) in enrichment_data.organization_relations.items():
                    enrichment_data.organization_relations[
                        org_name
                    ] = await _validate_infoscience_relations(
                        relations,
                    )
                logger.info(
                    f"✓ Validated organization relations for {len(enrichment_data.organization_relations)} organizations",
                )

        return result
    except Exception as e:
        logger.error(f"linked entities enrichment failed: {e}")
        # Return empty result instead of failing
        return {
            "data": linkedEntitiesEnrichmentResult(
                repository_relations=[],
                searchStrategy="Enrichment failed",
                totalSearches=0,
            ),
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


async def enrich_user_linked_entities(
    username: str,
    full_name: str,
    bio: str,
    organizations: list,
) -> dict:
    """
    Enrich user with linked entities relations.

    Args:
        username: GitHub username
        full_name: User's full name
        bio: User's bio
        organizations: List of organizations

    Returns:
        Dictionary with 'data' (linkedEntitiesEnrichmentResult) and 'usage' keys
    """
    prompt = get_user_linked_entities_prompt(
        username=username,
        full_name=full_name,
        bio=bio,
        organizations=organizations,
    )

    logger.info(f"🔍 Starting linked entities enrichment for user: {username}")

    try:
        result = await run_agent_with_fallback(linked_entities_configs, prompt)

        if result and result.get("data"):
            enrichment_data = result["data"]

            # Validate Infoscience URLs in author relations
            logger.info("🔍 Validating Infoscience URLs in author relations...")
            for author_name, relations in enrichment_data.author_relations.items():
                enrichment_data.author_relations[
                    author_name
                ] = await _validate_infoscience_relations(
                    relations,
                )

            logger.info(
                f"✓ Found linked entities relations for {len(enrichment_data.author_relations)} authors",
            )

        return result
    except Exception as e:
        logger.error(f"linked entities enrichment failed: {e}")
        # Return empty result instead of failing
        return {
            "data": linkedEntitiesEnrichmentResult(
                author_relations={},
                searchStrategy="Enrichment failed",
                totalSearches=0,
            ),
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


async def enrich_organization_linked_entities(
    org_name: str,
    description: str,
    website: str,
    members: list,
) -> dict:
    """
    Enrich organization with linked entities relations.

    Args:
        org_name: Organization name
        description: Organization description
        website: Organization website
        members: List of member usernames

    Returns:
        Dictionary with 'data' (linkedEntitiesEnrichmentResult) and 'usage' keys
    """
    prompt = get_organization_linked_entities_prompt(
        org_name=org_name,
        description=description,
        website=website,
        members=members,
    )

    logger.info(f"🔍 Starting linked entities enrichment for organization: {org_name}")

    try:
        result = await run_agent_with_fallback(linked_entities_configs, prompt)

        if result and result.get("data"):
            enrichment_data = result["data"]

            # Validate Infoscience URLs in organization relations
            logger.info("🔍 Validating Infoscience URLs in organization relations...")
            for org_name, relations in enrichment_data.organization_relations.items():
                enrichment_data.organization_relations[
                    org_name
                ] = await _validate_infoscience_relations(
                    relations,
                )

            logger.info(
                f"✓ Found linked entities relations for {len(enrichment_data.organization_relations)} organizations",
            )

        return result
    except Exception as e:
        logger.error(f"linked entities enrichment failed: {e}")
        # Return empty result instead of failing
        return {
            "data": linkedEntitiesEnrichmentResult(
                organization_relations={},
                searchStrategy="Enrichment failed",
                totalSearches=0,
            ),
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
