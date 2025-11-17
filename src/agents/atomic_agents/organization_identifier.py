"""
Organization Identifier Agent - Atomic agent for identifying related organizations.

This agent analyzes compiled repository context and identifies organizations
that are related to the repository (developers, maintainers, sponsors, etc.).
"""

import logging
from typing import Any, Dict

from ...llm.model_config import load_model_config, validate_config
from ...utils.token_counter import estimate_tokens_from_messages
from ..agents_management import run_agent_with_fallback
from .models import CompiledContext, OrganizationIdentification

logger = logging.getLogger(__name__)

# Load model configurations for organization identification
ORGANIZATION_IDENTIFIER_CONFIGS = load_model_config("run_organization_identifier")

# Validate configurations
for config in ORGANIZATION_IDENTIFIER_CONFIGS:
    if not validate_config(config):
        logger.error(f"Invalid configuration for organization identifier: {config}")
        raise ValueError("Invalid model configuration")

# System prompt for organization identifier
ORGANIZATION_IDENTIFIER_SYSTEM_PROMPT = """
You are an expert at identifying institutional organizations directly related to software repositories.

Your task is to:
1. Analyze the compiled repository context provided.
2. Identify ONLY institutional organizations that are DIRECTLY related to this software/repository.
3. Determine the type of each organization (Research Institute, University, Company, Community Space, etc.) - **REQUIRED for each organization**.
4. Provide a confidence score (0.0 to 1.0) indicating how confident you are about the organization's relationship to the repository.
5. Provide clear justifications explaining how each organization is directly related to the repository.

**CRITICAL: Direct Relationship Required**
- Focus on organizations that have a DIRECT institutional relationship with the software itself
- A side affiliation of an author is NOT enough - the organization must be directly related to the software
- Examples of DIRECT relationships:
  - Organization develops, maintains, or owns the software
  - Organization funds or sponsors the software project
  - Organization hosts the repository or project
  - Organization is explicitly mentioned as a partner or collaborator on the software
  - Organization's research group or lab is directly associated with the software development
- Examples that are NOT sufficient:
  - An author happens to be affiliated with an organization (unless the organization is directly involved)
  - An organization is mentioned in passing without clear connection to the software
  - An organization is only related through a tangential author affiliation

**Types of Organizations to Look For:**
- **Developers/Maintainers**: Organizations that develop or maintain the repository
- **Sponsors/Funders**: Organizations that fund or sponsor the project
- **Host Organizations**: Organizations that host or own the repository
- **Institutional Partners**: Organizations explicitly mentioned as partners or collaborators
- **Research Groups/Labs**: Research groups or labs directly associated with the software development

**Organization Types:**
- Research Institute
- University
- Government Agency
- Private Company
- Non-Profit Organization
- Community Space
- Software Project
- Research Infrastructure
- etc.

**Important:**
- Extract organization names from README, documentation, funding acknowledgments, GitHub organization memberships
- Look for GitHub organization URLs (e.g., https://github.com/orgname)
- Check for explicit mentions in documentation, funding sections, acknowledgments
- Provide specific evidence-based justifications that demonstrate DIRECT relationship
- Only include organizations with clear, direct institutional connection to the software
- Each justification should reference specific evidence from the repository showing direct relationship

**Output Format:**
Return a JSON object matching the OrganizationIdentification schema exactly.
"""


def get_organization_identifier_prompt(compiled_context: CompiledContext) -> str:
    """
    Generate prompt for organization identifier agent.

    Args:
        compiled_context: Compiled repository context from context compiler

    Returns:
        Formatted prompt string
    """
    prompt = f"""Identify institutional organizations DIRECTLY related to the following software repository:

**Repository URL:** {compiled_context.repository_url}

**Compiled Repository Context:**
{compiled_context.markdown_content}

**IMPORTANT:** Only identify organizations that have a DIRECT institutional relationship with the software itself. A side affiliation of an author is NOT sufficient - the organization must be directly involved with the software development, funding, hosting, or partnership.

Please identify:
1. Institutional organizations DIRECTLY related to this software (developers, maintainers, sponsors, hosts, institutional partners)
2. The type of each organization (REQUIRED - e.g., 'Research Institute', 'University', 'Company', etc.)
3. Organization identifiers (GitHub URLs, websites, etc.)
4. Confidence score (0.0 to 1.0) for each organization's relationship to the repository

For each organization, provide:
- The organization type (REQUIRED)
- A confidence score indicating how certain you are about the relationship
- Clear justifications that demonstrate the DIRECT relationship with the software, referencing specific evidence from the repository context
"""

    logger.debug(f"Organization identifier prompt length: {len(prompt)} chars")
    return prompt


async def identify_related_organizations(
    compiled_context: CompiledContext,
) -> Dict[str, Any]:
    """
    Identify organizations related to the repository using an atomic agent.

    Args:
        compiled_context: Compiled markdown content with all repository information

    Returns:
        Dictionary with 'data' (OrganizationIdentification) and 'usage' (dict with token info)
    """
    logger.info(
        f"Identifying related organizations for {compiled_context.repository_url}",
    )

    # Prepare the prompt
    prompt = get_organization_identifier_prompt(compiled_context)

    # Create agent context
    agent_context = {
        "repository_url": compiled_context.repository_url,
        "compiled_context": compiled_context.markdown_content,
    }

    # No tools needed for identification
    tools = []

    logger.debug(f"Prompt length: {len(prompt)} characters")

    # Run agent with schema enforcement
    result = await run_agent_with_fallback(
        ORGANIZATION_IDENTIFIER_CONFIGS,
        prompt,
        agent_context,
        OrganizationIdentification,  # Schema enforcement
        ORGANIZATION_IDENTIFIER_SYSTEM_PROMPT,
        tools,
    )

    # Extract the identification from PydanticAI result
    # Check if result has an .output attribute (PydanticAI wrapper)
    if hasattr(result, "output"):
        identification_data = result.output
    else:
        identification_data = result

    # Extract usage statistics from result attributes
    usage_data = {}
    input_tokens = 0
    output_tokens = 0

    if hasattr(result, "usage"):
        usage = result.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0

        # Fallback to details field for certain models
        if input_tokens == 0 and output_tokens == 0 and hasattr(usage, "details"):
            details = usage.details
            if isinstance(details, dict):
                input_tokens = details.get("input_tokens", 0)
                output_tokens = details.get("output_tokens", 0)

        logger.info(
            f"Organization identification usage: {input_tokens} input, {output_tokens} output tokens",
        )
    else:
        logger.warning("No usage data available from agent")

    usage_data["input_tokens"] = input_tokens
    usage_data["output_tokens"] = output_tokens

    # Estimate tokens with tiktoken (serialize model properly)
    response_text = ""
    if hasattr(identification_data, "model_dump_json"):
        response_text = identification_data.model_dump_json()
    elif isinstance(identification_data, dict):
        import json as json_module

        response_text = json_module.dumps(identification_data)
    elif isinstance(identification_data, str):
        response_text = identification_data

    estimated = estimate_tokens_from_messages(
        system_prompt=ORGANIZATION_IDENTIFIER_SYSTEM_PROMPT,
        user_prompt=prompt,
        response=response_text,
    )
    usage_data["estimated_input_tokens"] = estimated.get("input_tokens", 0)
    usage_data["estimated_output_tokens"] = estimated.get("output_tokens", 0)

    logger.info(
        f"Identified {len(identification_data.relatedToOrganizations)} related organizations",
    )

    return {
        "data": identification_data,
        "usage": usage_data,
    }
