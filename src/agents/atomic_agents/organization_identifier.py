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

# System prompt for organization identifier (generic - works for both repositories and users)
ORGANIZATION_IDENTIFIER_SYSTEM_PROMPT = """
You are an expert at identifying institutional organizations related to software repositories or users.

Your task is to:
1. Analyze the compiled context provided (repository or user context).
2. Identify institutional organizations that are related to the repository or user.
3. Determine the type of each organization (Research Institute, University, Company, Community Space, etc.) - **REQUIRED for each organization**.
4. Provide a confidence score (0.0 to 1.0) indicating how confident you are about the organization's relationship.
5. Provide clear justifications explaining how each organization is related.

**For Repositories:**
- Focus on organizations that have a DIRECT institutional relationship with the software itself
- A side affiliation of an author is NOT enough - the organization must be directly related to the software
- Examples: developers, maintainers, sponsors, hosts, institutional partners, research groups/labs directly associated with the software

**For Users:**
- Focus on organizations that the user is affiliated with
- Examples: current or past employers, universities, research institutes, labs, companies, organizations mentioned in bio/ORCID/GitHub profile

**For Organizations:**
- Focus on organizations that are related to this organization
- Examples: parent organizations, partner organizations, affiliated organizations, founding organizations, collaborating institutions

**Organization Types (REQUIRED for each organization):**
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
- Extract organization names from the provided context (README, documentation, bio, ORCID, GitHub, etc.)
- Look for GitHub organization URLs (e.g., https://github.com/orgname)
- Check for explicit mentions in documentation, funding sections, acknowledgments, bio, ORCID records
- Provide specific evidence-based justifications that demonstrate the relationship
- Each justification should reference specific evidence from the context

**Output Format:**
Return a JSON object matching the OrganizationIdentification schema exactly. The schema requires:
- relatedToOrganizations: List of SimplifiedOrganization objects, each with:
  - name: Organization name (REQUIRED)
  - organizationType: Type of organization (REQUIRED - must be a string like "Research Institute", "University", etc.)
  - id: Optional organization identifier (GitHub URL, website, etc.)
  - attributionConfidence: Optional confidence score (0.0 to 1.0)
- relatedToOrganizationJustification: List of justification strings (one per organization)
"""


def get_organization_identifier_prompt(
    compiled_context: CompiledContext,
    context_type: str = "repository",
) -> str:
    """
    Generate prompt for organization identifier agent.

    Args:
        compiled_context: Compiled context from context compiler
        context_type: Type of context - "repository", "user", or "organization"

    Returns:
        Formatted prompt string
    """
    if context_type == "organization":
        url_label = "Organization Profile URL"
    elif context_type == "user":
        url_label = "User Profile URL"
    else:
        url_label = "Repository URL"

    if context_type == "organization":
        prompt = f"""Identify institutional organizations that are related to this organization:

**{url_label}:** {compiled_context.repository_url}

**Compiled Organization Context:**
{compiled_context.markdown_content}

**IMPORTANT:** Identify organizations that are related to this organization, such as:
- Parent organizations (e.g., a lab's parent university)
- Partner organizations (collaborating institutions)
- Affiliated organizations (organizations this org is part of or works with)
- Founding organizations (if this org was established by other orgs)
- Organizations mentioned in the organization's description, README, or metadata

Please identify:
1. Institutional organizations related to this organization
2. The type of each organization (REQUIRED - e.g., 'Research Institute', 'University', 'Company', etc.)
3. Organization identifiers (GitHub URLs, websites, ROR IDs, etc.)
4. Confidence score (0.0 to 1.0) for each organization's relationship

For each organization, provide:
- The organization type (REQUIRED)
- A confidence score indicating how certain you are about the relationship
- Clear justifications that demonstrate the relationship, referencing specific evidence from the organization context
"""
    elif context_type == "user":
        prompt = f"""Identify institutional organizations that this user is affiliated with:

**{url_label}:** {compiled_context.repository_url}

**Compiled User Context:**
{compiled_context.markdown_content}

**IMPORTANT:** Identify organizations that the user is affiliated with, such as:
- Current or past employers
- Universities or educational institutions
- Research institutes or labs
- Companies or organizations they work for
- Organizations mentioned in their bio, ORCID, or GitHub profile

Please identify:
1. Institutional organizations the user is affiliated with
2. The type of each organization (REQUIRED - e.g., 'Research Institute', 'University', 'Company', etc.)
3. Organization identifiers (GitHub URLs, websites, ROR IDs, etc.)
4. Confidence score (0.0 to 1.0) for each organization's relationship to the user

For each organization, provide:
- The organization type (REQUIRED)
- A confidence score indicating how certain you are about the affiliation
- Clear justifications that demonstrate the user's affiliation, referencing specific evidence from the user context
"""
    else:
        prompt = f"""Identify institutional organizations DIRECTLY related to the following software repository:

**{url_label}:** {compiled_context.repository_url}

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
    context_type: str = "repository",
) -> Dict[str, Any]:
    """
    Identify organizations related to the repository or user using an atomic agent.

    Args:
        compiled_context: Compiled markdown content with all repository/user/organization information
        context_type: Type of context - "repository", "user", or "organization" (default: "repository")

    Returns:
        Dictionary with 'data' (OrganizationIdentification) and 'usage' (dict with token info)
    """
    logger.info(
        f"Identifying related organizations for {compiled_context.repository_url} (context_type: {context_type})",
    )

    # Prepare the prompt
    prompt = get_organization_identifier_prompt(
        compiled_context,
        context_type=context_type,
    )

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
