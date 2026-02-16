"""
URL Validation Agent

Uses agent delegation to validate ROR IDs and Infoscience URLs by fetching HTML content
and using a lightweight validation agent to verify entity matches.
"""

import json
import logging
from typing import Any, Dict, Optional

import httpx
from pydantic_ai import Agent, RunContext

from ..data_models.validation import ValidationResult
from ..llm.model_config import (
    create_pydantic_ai_model,
    get_retry_delay,
    load_model_config,
    validate_config,
)
from .validation_utils import (
    fetch_html_content,
    normalize_infoscience_url,
)

logger = logging.getLogger(__name__)

# Load model configuration for validation agent
validation_configs = load_model_config("run_url_validation")

# Validate configurations
for config in validation_configs:
    if not validate_config(config):
        logger.error(f"Invalid configuration for URL validation: {config}")
        raise ValueError("Invalid model configuration")

# Agent cleanup tracking
_active_validation_agents = []

# Validation agent system prompt (generic - specific instructions in user prompts)
validation_system_prompt = """
You are an expert at validating URLs by analyzing content to verify they match expected entities.

Your task is to:
1. Analyze the content retrieved from a URL (HTML, JSON, or other data formats)
2. Compare it with the expected entity information provided in the user prompt
3. Determine if the URL actually points to the correct entity
4. Provide a confidence score (0.0-1.0) and clear justification

Follow the specific validation instructions provided in the user prompt for this validation type.
Be conservative - if there's any doubt, set is_valid to false and provide clear justification.
High confidence (>= 0.8) should only be used when there's clear, unambiguous match.
"""


def create_validation_agent(config: dict) -> Agent:
    """Create a URL validation agent from configuration."""
    model = create_pydantic_ai_model(config)

    agent = Agent(
        model=model,
        output_type=ValidationResult,
        system_prompt=validation_system_prompt,
        tools=[],  # Pure analysis agent, no tools
    )

    # Track agent for cleanup
    _active_validation_agents.append(agent)

    return agent


async def cleanup_validation_agents():
    """Cleanup validation agents to free memory."""
    global _active_validation_agents
    for agent in _active_validation_agents:
        try:
            if hasattr(agent, "close"):
                await agent.close()
        except Exception as e:
            logger.warning(f"Error cleaning up validation agent: {e}")
    _active_validation_agents = []


async def run_validation_agent_with_fallback(
    prompt: str,
    ctx: Optional[RunContext] = None,
) -> ValidationResult:
    """
    Run the validation agent with fallback across multiple models.

    Args:
        prompt: The validation prompt
        ctx: Optional RunContext for agent delegation (provides deps and usage)

    Returns:
        ValidationResult
    """
    last_exception = None

    for config_idx, config in enumerate(validation_configs):
        try:
            agent = create_validation_agent(config)
            logger.debug(
                f"Attempting validation with {config['provider']}/{config['model']}",
            )

            # Prepare run parameters
            # Prompt must be positional argument, not keyword
            # Model parameters (temperature, max_tokens) are set on the model
            # when created via create_pydantic_ai_model(), not passed to run()
            run_kwargs = {}

            # If context provided, use it for delegation
            if ctx is not None:
                run_kwargs["deps"] = ctx.deps
                run_kwargs["usage"] = ctx.usage

            # Run agent - prompt is positional, deps and usage are keyword
            result = await agent.run(prompt, **run_kwargs)

            # Return ValidationResult directly (output_type is already ValidationResult)
            return result.output

        except Exception as e:
            last_exception = e
            logger.warning(
                f"Validation failed with {config['provider']}/{config['model']}: {e}",
            )
            if config_idx < len(validation_configs) - 1:
                delay = get_retry_delay(config_idx)
                logger.info(f"Retrying validation in {delay}s with next model...")
                import asyncio

                await asyncio.sleep(delay)

    raise last_exception or Exception("All validation models failed")


async def validate_ror_url(
    ror_id: str,
    expected_org: Dict[str, Any],
    ctx: Optional[RunContext] = None,
) -> ValidationResult:
    """
    Validate a ROR URL by fetching JSON from ROR API and checking if it matches the expected organization.

    Args:
        ror_id: ROR ID (can be full URL like "https://ror.org/05gzmn429" or just "05gzmn429")
        expected_org: Dictionary with expected organization data (name, country, type, website, etc.)
        ctx: Optional RunContext for agent delegation

    Returns:
        ValidationResult
    """
    logger.info(
        f"Validating ROR URL: {ror_id} for organization: {expected_org.get('name', 'Unknown')}",
    )

    # Extract ROR ID from URL if needed
    if ror_id.startswith("http://") or ror_id.startswith("https://"):
        # Extract ID from URL (e.g., "https://ror.org/05gzmn429" -> "05gzmn429")
        ror_id_clean = ror_id.split("/")[-1]
        ror_api_url = f"https://api.ror.org/v2/organizations/{ror_id_clean}"
        logger.debug(f"Extracted ROR ID '{ror_id_clean}' from full URL '{ror_id}'")
    else:
        ror_id_clean = ror_id
        ror_api_url = f"https://api.ror.org/v2/organizations/{ror_id_clean}"
        logger.debug(f"Using ROR ID '{ror_id_clean}' directly (not a full URL)")

    logger.debug(f"Fetching ROR data from API: {ror_api_url}")

    try:
        # Fetch JSON from ROR API v2 endpoint
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(ror_api_url)
            response.raise_for_status()
            ror_data = response.json()

        # Extract organization name from names array
        org_name = None
        if ror_data.get("names"):
            for name_entry in ror_data.get("names", []):
                if "ror_display" in name_entry.get("types", []):
                    org_name = name_entry.get("value")
                    break
            if not org_name:
                for name_entry in ror_data.get("names", []):
                    if "label" in name_entry.get("types", []):
                        org_name = name_entry.get("value")
                        break

        # Extract country from locations
        country = None
        if ror_data.get("locations"):
            country = (
                ror_data.get("locations", [{}])[0]
                .get("geonames_details", {})
                .get("country_name")
            )

        # Extract aliases
        aliases = []
        if ror_data.get("names"):
            for name_entry in ror_data.get("names", []):
                if name_entry.get("value") != org_name:
                    aliases.append(name_entry.get("value"))

        # Extract website
        website = None
        if ror_data.get("links"):
            for link in ror_data.get("links", []):
                if link.get("type") == "website":
                    website = link.get("value")
                    break

        # Format ROR data for validation prompt
        ror_data_summary = f"""ROR API Data:
- Name: {org_name or 'N/A'}
- Country: {country or 'N/A'}
- Website: {website or 'N/A'}
- Aliases: {', '.join(aliases[:10])}  # Limit to first 10
- Established: {ror_data.get('established') or 'N/A'}
- Relationships: {len(ror_data.get('relationships', []))} relationships found
- Full JSON (for reference): {json.dumps(ror_data, indent=2)[:3000]}  # Limit to first 3000 chars
"""

        # Prepare validation prompt with ROR-specific instructions
        prompt = f"""Validate if this ROR ID matches the expected organization.

**Validation Type:** ROR (Research Organization Registry) - JSON API validation

**ROR Validation Instructions:**
- Check if the organization name matches (exact or partial matches are acceptable)
- Verify country matches expectations
- Verify website matches (if provided)
- Look for aliases and alternate names in the names array
- Consider partial matches (e.g., "EPFL" vs "École Polytechnique Fédérale de Lausanne")
- Analyze the JSON structure from the ROR API v2 endpoint

ROR ID: {ror_id_clean}
ROR API URL: {ror_api_url}

Expected Organization:
- Name: {expected_org.get('name') or 'N/A'}
- Country: {expected_org.get('country') or 'N/A'}
- Website: {expected_org.get('website') or 'N/A'}
- Aliases: {', '.join(expected_org.get('aliases', []))}

{ror_data_summary}

Analyze the ROR API data and determine:
1. Does the organization name match? (Check names array for exact matches or variations)
2. Does the country match?
3. Are there any aliases or alternate names that match?
4. Does the website match (if provided)?

Provide a clear validation result with confidence score and justification.
"""

        # Run validation agent
        result = await run_validation_agent_with_fallback(prompt, ctx)

        logger.info(
            f"ROR validation result for {ror_api_url}: valid={result.is_valid}, "
            f"confidence={result.confidence:.2f}",
        )

        return result

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning(f"ROR ID {ror_id_clean} does not exist in ROR API (404)")
            return ValidationResult(
                is_valid=False,
                confidence=0.0,
                justification=f"ROR ID {ror_id_clean} does not exist in ROR API (404 error)",
                validation_errors=["HTTP 404: ROR ID not found"],
            )
        else:
            logger.error(f"HTTP error fetching ROR API data: {e.response.status_code}")
            return ValidationResult(
                is_valid=False,
                confidence=0.0,
                justification=f"Error fetching ROR API data: HTTP {e.response.status_code}",
                validation_errors=[f"HTTP {e.response.status_code}"],
            )
    except Exception as e:
        logger.error(f"Error validating ROR URL {ror_api_url}: {e}", exc_info=True)
        return ValidationResult(
            is_valid=False,
            confidence=0.0,
            justification=f"Error during validation: {e!s}",
            validation_errors=[str(e)],
        )


async def validate_infoscience_url(
    url: str,
    expected_entity: Dict[str, Any],
    entity_type: str,
    ctx: Optional[RunContext] = None,
) -> ValidationResult:
    """
    Validate an Infoscience URL by fetching HTML and checking if it matches the expected entity.

    Args:
        url: Infoscience URL (can be UUID, partial URL, or full URL)
        expected_entity: Dictionary with expected entity data (name, title, authors, etc.)
        entity_type: Type of entity ("publication", "person", "orgunit")
        ctx: Optional RunContext for agent delegation

    Returns:
        ValidationResult with normalized_url if URL was normalized
    """
    logger.info(
        f"Validating Infoscience URL: {url} for {entity_type}: {expected_entity.get('name') or expected_entity.get('title', 'Unknown')}",
    )

    try:
        # Normalize URL first
        normalized_url = normalize_infoscience_url(url, entity_type)

        if not normalized_url:
            logger.warning(f"Could not normalize Infoscience URL: {url}")
            return ValidationResult(
                is_valid=False,
                confidence=0.0,
                justification=f"Invalid URL format: {url}",
                validation_errors=[f"Could not normalize URL: {url}"],
            )

        # Fetch HTML content
        html_content = await fetch_html_content(normalized_url)

        # Prepare validation prompt based on entity type with Infoscience-specific instructions
        if entity_type == "publication":
            prompt = f"""Validate if this Infoscience publication URL matches the expected publication.

**Validation Type:** Infoscience (EPFL repository) - HTML/Markdown validation for publications

**Infoscience Publication Validation Instructions:**
- Verify title matches (exact or close match acceptable)
- Verify expected authors are present in the author list
- Verify DOI matches (if provided)
- Verify publication date matches (if provided)
- Verify lab/affiliation matches (if provided)
- Analyze the markdown content extracted from the HTML page
- Consider that URLs may have been normalized from UUIDs or handles

Infoscience URL: {normalized_url}

Expected Publication:
- Title: {expected_entity.get('title') or 'N/A'}
- Authors: {', '.join(expected_entity.get('authors') or [])}
- DOI: {expected_entity.get('doi') or 'N/A'}
- Publication Date: {expected_entity.get('publication_date') or 'N/A'}
- Lab: {expected_entity.get('lab') or 'N/A'}

Markdown Content Retrieved from Infoscience URL (HTML converted to markdown):
{html_content[:5000]}

Analyze the markdown content and determine:
1. Does the publication title match?
2. Are the expected authors present?
3. Does the DOI match (if provided)?
4. Does the publication date match?
5. Does the lab/affiliation match?

Provide a clear validation result with confidence score and justification.
"""
        elif entity_type == "person":
            prompt = f"""Validate if this Infoscience person URL matches the expected person.

**Validation Type:** Infoscience (EPFL repository) - HTML/Markdown validation for persons

**Infoscience Person Validation Instructions:**
- Verify name matches (exact or close match acceptable)
- Verify affiliation matches (if provided)
- Verify ORCID matches (if provided)
- Verify email matches (if provided)
- Analyze the markdown content extracted from the HTML page
- Consider that URLs may have been normalized from UUIDs or handles

Infoscience URL: {normalized_url}

Expected Person:
- Name: {expected_entity.get('name') or 'N/A'}
- Affiliation: {expected_entity.get('affiliation') or 'N/A'}
- ORCID: {expected_entity.get('orcid') or 'N/A'}
- Email: {expected_entity.get('email') or 'N/A'}

Markdown Content Retrieved from Infoscience URL (HTML converted to markdown):
{html_content[:5000]}

Analyze the markdown content and determine:
1. Does the person name match?
2. Does the affiliation match?
3. Does the ORCID match (if provided)?
4. Does the email match (if provided)?

Provide a clear validation result with confidence score and justification.
"""
        elif entity_type == "orgunit":
            prompt = f"""Validate if this Infoscience organizational unit URL matches the expected orgunit.

**Validation Type:** Infoscience (EPFL repository) - HTML/Markdown validation for organizational units

**Infoscience Organizational Unit Validation Instructions:**
- Verify name matches (exact or close match acceptable)
- Verify parent organization matches (if provided)
- Verify description matches (if provided)
- Analyze the markdown content extracted from the HTML page
- Consider that URLs may have been normalized from UUIDs or handles

Infoscience URL: {normalized_url}

Expected Organizational Unit:
- Name: {expected_entity.get('name') or 'N/A'}
- Parent Organization: {expected_entity.get('parent_organization') or 'N/A'}
- Description: {str(expected_entity.get('description') or 'N/A')[:200]}...

Markdown Content Retrieved from Infoscience URL (HTML converted to markdown):
{html_content[:5000]}

Analyze the markdown content and determine:
1. Does the organizational unit name match?
2. Does the parent organization match?
3. Does the description match?

Provide a clear validation result with confidence score and justification.
"""
        else:
            return ValidationResult(
                is_valid=False,
                confidence=0.0,
                justification=f"Unknown entity type: {entity_type}",
                validation_errors=[f"Unknown entity type: {entity_type}"],
            )

        # Run validation agent
        result = await run_validation_agent_with_fallback(prompt, ctx)

        # Add normalized URL to result
        if normalized_url != url:
            result.normalized_url = normalized_url
            logger.info(f"Normalized Infoscience URL: {url} -> {normalized_url}")

        logger.info(
            f"Infoscience validation result for {normalized_url}: valid={result.is_valid}, "
            f"confidence={result.confidence:.2f}",
        )

        return result

    except Exception as e:
        logger.error(f"Error validating Infoscience URL {url}: {e}", exc_info=True)
        return ValidationResult(
            is_valid=False,
            confidence=0.0,
            justification=f"Error during validation: {e!s}",
            validation_errors=[str(e)],
        )
