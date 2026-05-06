"""
linked entities Enrichment Agent

This agent searches academic catalogs (Infoscience, OpenAlex, EPFL Graph) to find
related publications, persons, and organizational units.
"""

import logging
from typing import Any

from pydantic import HttpUrl, ValidationError
from pydantic_ai import Agent

from ..context.infoscience import (
    clear_infoscience_cache,
    get_author_publications_tool,
    search_infoscience_authors_tool,
    search_infoscience_labs_tool,
    search_infoscience_publications_tool,
)
from ..data_models.conversion import create_simplified_model
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

# Create simplified model for linked entities enrichment
# This converts HttpUrl fields to str with format instructions
(
    _SIMPLIFIED_LINKED_ENTITIES_MODEL,
    _LINKED_ENTITIES_UNION_METADATA,
) = create_simplified_model(linkedEntitiesEnrichmentResult)

# Track active agents for cleanup
_active_linked_entities_agents = []


def _convert_simplified_to_full_linked_entities(
    simplified_output: Any,
) -> linkedEntitiesEnrichmentResult:
    """
    Convert simplified linked entities output (with str URLs) to full model (with HttpUrl).

    Args:
        simplified_output: Simplified model output with string URLs

    Returns:
        Full linkedEntitiesEnrichmentResult with HttpUrl fields validated
    """
    logger.debug(f"Simplified output from LLM: {simplified_output}")
    # Convert to dict if it's a Pydantic model
    if hasattr(simplified_output, "model_dump"):
        data = simplified_output.model_dump()
    elif isinstance(simplified_output, dict):
        data = simplified_output
    else:
        data = simplified_output

    # Recursively convert URL strings to HttpUrl in nested entities
    def convert_urls_in_entity(entity_dict: dict, entity_type: str = None) -> dict:
        """Convert URL strings to HttpUrl in entity dictionaries."""
        if not isinstance(entity_dict, dict):
            return entity_dict

        converted = entity_dict.copy()

        # Clean up None values in list fields (convert to empty list)
        for list_field in ["subjects", "authors", "keywords", "research_areas"]:
            if list_field in converted and converted[list_field] is None:
                converted[list_field] = []

        # Construct URL from UUID if URL is missing
        uuid = converted.get("uuid")
        if uuid and not converted.get("url") and not converted.get("profile_url"):
            # Construct URL based on entity type
            if (
                entity_type == "publication"
                or "publication" in str(converted.get("type", "")).lower()
            ):
                converted[
                    "url"
                ] = f"https://infoscience.epfl.ch/entities/publication/{uuid}"
            elif (
                entity_type == "person"
                or "author" in str(converted.get("type", "")).lower()
            ):
                converted[
                    "profile_url"
                ] = f"https://infoscience.epfl.ch/entities/person/{uuid}"
            elif (
                entity_type == "orgunit"
                or "orgunit" in str(converted.get("type", "")).lower()
                or "lab" in str(converted.get("type", "")).lower()
            ):
                converted[
                    "url"
                ] = f"https://infoscience.epfl.ch/entities/orgunit/{uuid}"

        # Convert url fields to HttpUrl
        for url_field in ["url", "profile_url", "repository_url"]:
            if url_field in converted and converted[url_field]:
                try:
                    # Validate and convert string to HttpUrl
                    converted[url_field] = HttpUrl(converted[url_field])
                except (ValueError, ValidationError) as e:
                    logger.warning(
                        f"Invalid URL format for {url_field}: {converted[url_field]}, error: {e}",
                    )
                    # Keep as string if validation fails (will be handled by model validation)

        # Recursively convert nested entities
        if "entityInfosciencePublication" in converted:
            converted["entityInfosciencePublication"] = convert_urls_in_entity(
                converted["entityInfosciencePublication"],
                entity_type="publication",
            )
        if "entityInfoscienceAuthor" in converted:
            converted["entityInfoscienceAuthor"] = convert_urls_in_entity(
                converted["entityInfoscienceAuthor"],
                entity_type="person",
            )
        if "entityInfoscienceOrgUnit" in converted:
            converted["entityInfoscienceOrgUnit"] = convert_urls_in_entity(
                converted["entityInfoscienceOrgUnit"],
                entity_type="orgunit",
            )

        return converted

    # Convert relations recursively
    def convert_relations(relations: list) -> list:
        """Convert URL strings in relations and reconcile Union fields."""
        converted_relations = []
        for rel in relations:
            if isinstance(rel, dict):
                # Then convert URLs to HttpUrl
                converted_rel = convert_urls_in_entity(
                    rel,
                    entity_type=rel.get("entityType"),
                )
                converted_relations.append(converted_rel)
            else:
                converted_relations.append(rel)
        return converted_relations

    # Convert repository_relations
    if "repository_relations" in data and data["repository_relations"]:
        data["repository_relations"] = convert_relations(data["repository_relations"])

    # Convert author_relations (dict of lists)
    if "author_relations" in data and data["author_relations"]:
        converted_author_relations = {}
        for author_name, relations in data["author_relations"].items():
            converted_author_relations[author_name] = convert_relations(relations)
        data["author_relations"] = converted_author_relations

    # Convert organization_relations (dict of lists)
    if "organization_relations" in data and data["organization_relations"]:
        converted_org_relations = {}
        for org_name, relations in data["organization_relations"].items():
            converted_org_relations[org_name] = convert_relations(relations)
        data["organization_relations"] = converted_org_relations

    # Create full model from converted data
    # First, validate and create linkedEntitiesRelation objects from the relations
    try:
        # Convert repository_relations
        converted_repo_relations = []
        if "repository_relations" in data and data["repository_relations"]:
            for rel_dict in data["repository_relations"]:
                # Apply None-to-empty-list conversion to nested entities
                if rel_dict.get("entityInfosciencePublication"):
                    rel_dict["entityInfosciencePublication"] = convert_urls_in_entity(
                        rel_dict["entityInfosciencePublication"],
                        entity_type="publication",
                    )
                if rel_dict.get("entityInfoscienceAuthor"):
                    rel_dict["entityInfoscienceAuthor"] = convert_urls_in_entity(
                        rel_dict["entityInfoscienceAuthor"],
                        entity_type="person",
                    )
                if rel_dict.get("entityInfoscienceOrgUnit"):
                    rel_dict["entityInfoscienceOrgUnit"] = convert_urls_in_entity(
                        rel_dict["entityInfoscienceOrgUnit"],
                        entity_type="orgunit",
                    )
                try:
                    converted_repo_relations.append(linkedEntitiesRelation(**rel_dict))
                except ValidationError as e:
                    logger.warning(
                        f"Failed to create repository relation: {e}, skipping relation",
                    )
                    logger.debug(f"Failed relation dict: {rel_dict}")

        # Convert author_relations (dict of lists)
        converted_author_relations = {}
        if "author_relations" in data and data["author_relations"]:
            for author_name, relations in data["author_relations"].items():
                converted_author_relations[author_name] = []
                for rel_dict in relations:
                    # Apply None-to-empty-list conversion to nested entities
                    if rel_dict.get("entityInfosciencePublication"):
                        rel_dict[
                            "entityInfosciencePublication"
                        ] = convert_urls_in_entity(
                            rel_dict["entityInfosciencePublication"],
                            entity_type="publication",
                        )
                    if rel_dict.get("entityInfoscienceAuthor"):
                        rel_dict["entityInfoscienceAuthor"] = convert_urls_in_entity(
                            rel_dict["entityInfoscienceAuthor"],
                            entity_type="person",
                        )
                    if rel_dict.get("entityInfoscienceOrgUnit"):
                        rel_dict["entityInfoscienceOrgUnit"] = convert_urls_in_entity(
                            rel_dict["entityInfoscienceOrgUnit"],
                            entity_type="orgunit",
                        )
                    try:
                        converted_author_relations[author_name].append(
                            linkedEntitiesRelation(**rel_dict),
                        )
                    except ValidationError as e:
                        logger.warning(
                            f"Failed to create author relation for {author_name}: {e}, skipping relation",
                        )
                        logger.debug(f"Failed relation dict: {rel_dict}")

        # Convert organization_relations (dict of lists)
        converted_org_relations = {}
        if "organization_relations" in data and data["organization_relations"]:
            for org_name, relations in data["organization_relations"].items():
                converted_org_relations[org_name] = []
                for rel_dict in relations:
                    # Apply None-to-empty-list conversion to nested entities
                    if rel_dict.get("entityInfosciencePublication"):
                        rel_dict[
                            "entityInfosciencePublication"
                        ] = convert_urls_in_entity(
                            rel_dict["entityInfosciencePublication"],
                            entity_type="publication",
                        )
                    if rel_dict.get("entityInfoscienceAuthor"):
                        rel_dict["entityInfoscienceAuthor"] = convert_urls_in_entity(
                            rel_dict["entityInfoscienceAuthor"],
                            entity_type="person",
                        )
                    if rel_dict.get("entityInfoscienceOrgUnit"):
                        rel_dict["entityInfoscienceOrgUnit"] = convert_urls_in_entity(
                            rel_dict["entityInfoscienceOrgUnit"],
                            entity_type="orgunit",
                        )
                    try:
                        converted_org_relations[org_name].append(
                            linkedEntitiesRelation(**rel_dict),
                        )
                    except ValidationError as e:
                        logger.warning(
                            f"Failed to create organization relation for {org_name}: {e}, skipping relation",
                        )
                        logger.debug(f"Failed relation dict: {rel_dict}")

        # Create the full result with converted relations
        result = linkedEntitiesEnrichmentResult(
            repository_relations=converted_repo_relations,
            author_relations=converted_author_relations,
            organization_relations=converted_org_relations,
            searchStrategy=data.get("searchStrategy"),
            catalogsSearched=data.get("catalogsSearched", []),
            totalSearches=data.get("totalSearches", 0),
            inputTokens=data.get("inputTokens"),
            outputTokens=data.get("outputTokens"),
        )

        # Populate the `entity` field for convenience
        for relation in result.relations:
            if relation.entityType == EntityType.PUBLICATION:
                relation.entity = relation.entityInfosciencePublication
            elif relation.entityType == EntityType.PERSON:
                relation.entity = relation.entityInfoscienceAuthor
            elif relation.entityType == EntityType.ORGUNIT:
                relation.entity = relation.entityInfoscienceOrgUnit

        return result
    except ValidationError as e:
        logger.error(
            f"Failed to create linkedEntitiesEnrichmentResult: {e}",
            exc_info=True,
        )
        logger.debug(f"Failed data: {data}")
        # Return a minimal valid result if conversion fails
        return linkedEntitiesEnrichmentResult(
            repository_relations=[],
            author_relations={},
            organization_relations={},
        )


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

    # Use simplified model that converts HttpUrl to str
    agent = Agent(
        model=model,
        output_type=_SIMPLIFIED_LINKED_ENTITIES_MODEL,
        system_prompt=linked_entities_system_prompt,
        tools=tools,
        retries=3,  # Allow model to retry up to 3 times on validation errors
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
            logger.info(f"Prompt length: {len(prompt)} chars")
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

            # Convert simplified output (with str URLs) back to full model (with HttpUrl)
            # The simplified model returns strings for URL fields, we need to convert them
            full_output = _convert_simplified_to_full_linked_entities(output)

            # Debug logging: Log what we got from the LLM
            if hasattr(full_output, "organization_relations"):
                logger.debug(
                    f"Organization relations after conversion: {len(full_output.organization_relations)} organizations",
                )
                for org_name, relations in full_output.organization_relations.items():
                    logger.debug(
                        f"  Organization '{org_name}': {len(relations)} relations",
                    )
                    for idx, rel in enumerate(relations):
                        entity = None
                        if rel.entityType == EntityType.PUBLICATION:
                            entity = rel.entityInfosciencePublication
                        elif rel.entityType == EntityType.PERSON:
                            entity = rel.entityInfoscienceAuthor
                        elif rel.entityType == EntityType.ORGUNIT:
                            entity = rel.entityInfoscienceOrgUnit

                        logger.debug(
                            f"    Relation {idx}: entityType={rel.entityType}, "
                            f"has_entity={entity is not None}",
                        )
                        if entity:
                            if hasattr(entity, "uuid"):
                                logger.debug(f"      Entity UUID: {entity.uuid}")
                            if hasattr(entity, "url"):
                                logger.debug(f"      Entity URL: {entity.url}")
                            if hasattr(entity, "profile_url"):
                                logger.debug(
                                    f"      Entity profile_url: {entity.profile_url}",
                                )

            return {"data": full_output, "usage": usage_data}

        except Exception as e:
            error_msg = str(e)
            logger.warning(
                f"linked entities enrichment failed with model {config.get('model')}: {e}",
            )

            # Log detailed validation errors
            if "validation" in error_msg.lower() or "retries" in error_msg.lower():
                logger.error(
                    f"Agent run failed with validation error: {e}",
                    exc_info=True,
                )

                # Traverse nested exception chains
                if hasattr(e, "__cause__") and e.__cause__:
                    logger.error(f"Underlying cause: {e.__cause__}")
                    cause = e.__cause__
                    depth = 0
                    while hasattr(cause, "__cause__") and cause.__cause__ and depth < 5:
                        cause = cause.__cause__
                        logger.error(f"Nested cause (depth {depth + 1}): {cause}")
                        depth += 1

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

        entity = None
        if relation.entityType == EntityType.PUBLICATION:
            entity = relation.entityInfosciencePublication
        elif relation.entityType == EntityType.PERSON:
            entity = relation.entityInfoscienceAuthor
        elif relation.entityType == EntityType.ORGUNIT:
            entity = relation.entityInfoscienceOrgUnit

        # Debug: Log relation details before validation
        logger.debug(
            f"Validating relation: entityType={relation.entityType}, "
            f"has_entity={entity is not None}, "
            f"entity_type={type(entity).__name__ if entity else 'None'}",
        )

        # Get entity URL using the get_url() method which handles all cases
        entity_url = None
        if entity:
            if hasattr(entity, "url"):
                entity_url = str(entity.url) if entity.url else None
            elif hasattr(entity, "profile_url"):
                entity_url = str(entity.profile_url) if entity.profile_url else None

        if not entity_url:
            display_name = "Unknown"
            if entity:
                if hasattr(entity, "title"):
                    display_name = entity.title or "Unknown"
                elif hasattr(entity, "name"):
                    display_name = entity.name or "Unknown"
            logger.warning(
                f"Skipping relation without URL: {display_name}, "
                f"entity_type={type(entity).__name__ if entity else 'None'}",
            )
            continue

        # Extract UUID from entity (handles both Pydantic models and dicts)
        entity_uuid = None
        if hasattr(entity, "uuid"):
            entity_uuid = entity.uuid
        elif isinstance(entity, dict):
            entity_uuid = entity.get("uuid")

        # For Infoscience, UUID is mandatory
        if not entity_uuid:
            display_name = "Unknown"
            if entity:
                if hasattr(entity, "title"):
                    display_name = entity.title or "Unknown"
                elif hasattr(entity, "name"):
                    display_name = entity.name or "Unknown"
            logger.warning(
                f"Skipping Infoscience relation without UUID: {display_name}",
            )
            continue

        try:
            # Prepare expected entity data based on entity type
            expected_entity = {}
            if relation.entityType == EntityType.PUBLICATION:
                if hasattr(entity, "title"):
                    expected_entity["title"] = entity.title
                    expected_entity["authors"] = getattr(entity, "authors", [])
                    expected_entity["doi"] = getattr(entity, "doi", None)
                    expected_entity["publication_date"] = getattr(
                        entity,
                        "publication_date",
                        None,
                    )
                    expected_entity["lab"] = getattr(entity, "lab", None)
                elif isinstance(entity, dict):
                    expected_entity = entity
            elif relation.entityType == EntityType.PERSON:
                if hasattr(entity, "name"):
                    expected_entity["name"] = entity.name
                    expected_entity["affiliation"] = getattr(
                        entity,
                        "affiliation",
                        None,
                    )
                    expected_entity["orcid"] = getattr(entity, "orcid", None)
                    expected_entity["email"] = getattr(entity, "email", None)
                elif isinstance(entity, dict):
                    expected_entity = entity
            elif relation.entityType == EntityType.ORGUNIT:
                if hasattr(entity, "name"):
                    expected_entity["name"] = entity.name
                    expected_entity["parent_organization"] = getattr(
                        entity,
                        "parent_organization",
                        None,
                    )
                    expected_entity["description"] = getattr(
                        entity,
                        "description",
                        None,
                    )
                elif isinstance(entity, dict):
                    expected_entity = entity

            # Validate Infoscience URL
            validation_result = await validate_infoscience_url(
                url=str(entity_url),
                expected_entity=expected_entity,
                entity_type=relation.entityType.value,
                ctx=None,
            )

            display_name = "Unknown"
            if entity:
                if hasattr(entity, "title"):
                    display_name = entity.title or "Unknown"
                elif hasattr(entity, "name"):
                    display_name = entity.name or "Unknown"

            if not validation_result.is_valid:
                logger.warning(
                    f"⚠ Infoscience validation failed for {display_name}: "
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
                if hasattr(entity, "url"):
                    entity.url = validation_result.normalized_url
                elif hasattr(entity, "profile_url"):
                    entity.profile_url = validation_result.normalized_url

            # Update confidence based on validation
            if validation_result.confidence < 0.6:
                logger.info(
                    f"⚠ Low confidence Infoscience match for {display_name}: "
                    f"confidence={validation_result.confidence:.2f}",
                )
                # Reduce relation confidence
                relation.confidence = min(
                    relation.confidence,
                    validation_result.confidence,
                )
            else:
                logger.info(
                    f"✓ Infoscience validation passed for {display_name}: "
                    f"confidence={validation_result.confidence:.2f}",
                )

            validated_relations.append(relation)

        except Exception as e:
            display_name = "Unknown"
            if entity:
                if hasattr(entity, "title"):
                    display_name = entity.title or "Unknown"
                elif hasattr(entity, "name"):
                    display_name = entity.name or "Unknown"
            logger.error(
                f"Error validating Infoscience URL for {display_name}: {e}",
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
    force_refresh: bool = False,
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

    # Clear Infoscience cache if force_refresh is True
    if force_refresh:
        clear_infoscience_cache()

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
    force_refresh: bool = False,
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

    # Clear Infoscience cache if force_refresh is True
    if force_refresh:
        clear_infoscience_cache()

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
    force_refresh: bool = False,
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

    # Clear Infoscience cache if force_refresh is True
    if force_refresh:
        clear_infoscience_cache()

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
