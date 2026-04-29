"""
EPFL final checker agent - Post-enrichment EPFL assessment using atomic agent pipeline.

This module implements a two-stage assessment for EPFL relationship after all enrichments:
1. Context compiler: Compiles enriched data into markdown document
2. Structured assessment: Analyzes markdown and produces EPFL assessment
"""

import json
import logging
from typing import Any, Dict

from ...llm.model_config import load_model_config, validate_config
from ...utils.token_counter import (
    estimate_tokens_from_messages,
    estimate_tokens_with_tools,
)
from ..agents_management import run_agent_with_fallback
from .models import EnrichedDataContext, EPFLAssessment

logger = logging.getLogger(__name__)

# Load model configurations for EPFL final assessment
EPFL_FINAL_CHECKER_CONFIGS = load_model_config("run_epfl_final_checker")

# Validate configurations
for config in EPFL_FINAL_CHECKER_CONFIGS:
    if not validate_config(config):
        logger.error(f"Invalid configuration for EPFL final checker: {config}")
        raise ValueError("Invalid model configuration")

# System prompt for context compilation stage
EPFL_CONTEXT_COMPILER_SYSTEM_PROMPT = """
You are an expert at compiling comprehensive information about software repositories and their relationships to EPFL (École Polytechnique Fédérale de Lausanne).

Your task is to:
1. Analyze the enriched repository data provided (with ORCID affiliations, ROR organizations, linked entities)
2. Extract and organize all information relevant to EPFL relationship assessment
3. Compile everything into a well-structured markdown document

**Focus on EPFL-relevant information:**
- Authors with @epfl.ch email addresses
- ORCID affiliations mentioning EPFL, SDSC, or EPFL labs
- Organizations with EPFL in their name or ROR data
- Linked entities (publications, author profiles) from Infoscience
- README mentions of EPFL, SDSC, or EPFL-related projects
- Related organizations that include EPFL

**Output Format:**
Return ONLY a comprehensive markdown document (plain text, not JSON) organized with clear sections:
- Repository Overview
- Authors and Affiliations (with EPFL connections highlighted)
- Organizations (with EPFL relationships)
- Linked Entities (Infoscience publications/profiles if available)
- Other EPFL Evidence (README mentions, etc.)

**IMPORTANT:** Return ONLY the markdown document as plain text. Do not wrap it in JSON, do not add explanatory text, do not use code blocks. Just return the raw markdown text.
"""

# System prompt for EPFL assessment stage
EPFL_ASSESSMENT_SYSTEM_PROMPT = """
You are an expert at assessing relationships between software repositories and EPFL (École Polytechnique Fédérale de Lausanne).

Your task is to:
1. Analyze the compiled enriched data provided
2. Systematically identify ALL evidence of EPFL relationship
3. Calculate cumulative confidence score based on evidence weights
4. Provide detailed justification listing all evidence found

**Evidence Types and Weights:**
- EMAIL_DOMAIN (@epfl.ch addresses): 0.4
- ORCID_EMPLOYMENT (ORCID employment at EPFL): 0.3
- INFOSCIENCE_ENTITY (found in Infoscience database): 0.4
- BIO_MENTION (mentions EPFL/SDSC in bio/description): 0.25
- README_MENTION (mentions EPFL/SDSC in README): 0.25
- COMPANY_FIELD (company field mentions EPFL): 0.25
- ORGANIZATION_MEMBERSHIP (member of EPFL GitHub orgs): 0.25
- RELATED_ORGANIZATION (related org is EPFL from ROR): 0.25
- LOCATION (location is Lausanne): 0.15

**Confidence Calculation:**
- Sum all evidence weights
- Cap at 1.0
- relatedToEPFL = true if confidence >= 0.5, false otherwise

**Output:**
- relatedToEPFL: Boolean (true if confidence >= 0.5)
- relatedToEPFLConfidence: Float (0.0 to 1.0)
- relatedToEPFLJustification: String (comprehensive list of all evidence with contributions)
"""


def get_epfl_context_compiler_prompt(enriched_data: Dict[str, Any]) -> str:
    """
    Generate prompt for EPFL context compilation.

    Args:
        enriched_data: Complete repository data with all enrichments

    Returns:
        Formatted prompt string
    """
    prompt = f"""Compile comprehensive information about this repository's relationship to EPFL.

**Enriched Repository Data:**
{json.dumps(enriched_data, indent=2, default=str)}

Please:
1. Analyze all the enriched data provided
2. Extract and organize information relevant to EPFL relationship assessment
3. Focus on authors, affiliations, organizations, linked entities, and any EPFL mentions
4. Compile everything into a well-structured markdown document

Highlight EPFL connections clearly in each section.

**IMPORTANT:** Return ONLY the markdown document as plain text. Do not wrap it in JSON, do not add explanatory text, do not use code blocks. Just return the raw markdown text.
"""

    return prompt


def get_epfl_assessment_prompt(enriched_context: EnrichedDataContext) -> str:
    """
    Generate prompt for EPFL assessment.

    Args:
        enriched_context: Compiled enriched data context

    Returns:
        Formatted prompt string
    """
    prompt = f"""Assess the EPFL relationship for this repository using ALL enriched data.

**Compiled Enriched Data:**
{enriched_context.markdown_content}

**Repository URL:** {enriched_context.repository_url}

Please:
1. Systematically examine ALL the enriched data
2. Identify EVERY piece of evidence related to EPFL
3. Calculate cumulative confidence score (sum of evidence weights, max 1.0)
4. Determine boolean based on confidence threshold (>= 0.5 = true, < 0.5 = false)
5. Write comprehensive justification listing all evidence with confidence contributions

Be thorough and explicit about all evidence found and how each contributes to the confidence score.
"""

    return prompt


async def compile_enriched_data_for_epfl(
    enriched_data: Dict[str, Any],
    repository_url: str,
) -> Dict[str, Any]:
    """
    Compile enriched repository data into markdown for EPFL assessment.

    Stage 1 of the EPFL final assessment pipeline.

    Args:
        enriched_data: Complete repository data with all enrichments
        repository_url: Repository URL

    Returns:
        Dictionary with 'data' (EnrichedDataContext) and 'usage' (dict with token info)
    """
    # Create context for the agent
    agent_context = {
        "enriched_data": enriched_data,
        "repository_url": repository_url,
    }

    # Prepare the prompt
    prompt = get_epfl_context_compiler_prompt(enriched_data)

    # No tools for context compilation - only analyze existing enriched data
    tools = []

    try:
        # Run agent with fallback across multiple models
        # Use str as output type - agent returns markdown text
        result = await run_agent_with_fallback(
            EPFL_FINAL_CHECKER_CONFIGS,
            prompt,
            agent_context,
            str,  # Simple string output - just markdown text
            EPFL_CONTEXT_COMPILER_SYSTEM_PROMPT,
            tools,
        )

        # Extract the markdown string from PydanticAI result
        if hasattr(result, "output"):
            markdown_content = result.output
        else:
            markdown_content = result

        # Convert string to EnrichedDataContext
        if isinstance(markdown_content, str):
            enriched_context = EnrichedDataContext(
                markdown_content=markdown_content,
                repository_url=repository_url,
                summary=None,
            )
        else:
            # Fallback if we get something unexpected
            enriched_context = EnrichedDataContext(
                markdown_content=str(markdown_content),
                repository_url=repository_url,
                summary=None,
            )

        # Estimate tokens from prompt and response
        response_text = (
            markdown_content
            if isinstance(markdown_content, str)
            else str(markdown_content)
        )

        estimated = estimate_tokens_from_messages(
            system_prompt=EPFL_CONTEXT_COMPILER_SYSTEM_PROMPT,
            user_prompt=prompt,
            response=response_text,
        )

        # Extract usage information from the result
        usage_data = None

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

            usage_data = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_input_tokens": estimated.get("input_tokens", 0),
                "estimated_output_tokens": estimated.get("output_tokens", 0),
            }

        logger.info("EPFL enriched data context compilation completed successfully")

        # Debug: Log the compiled markdown content
        if hasattr(enriched_context, "markdown_content"):
            logger.debug("=" * 80)
            logger.debug("EPFL ENRICHED CONTEXT MARKDOWN (Stage 1 Output):")
            logger.debug("=" * 80)
            logger.debug(
                enriched_context.markdown_content[:1000] + "..."
                if len(enriched_context.markdown_content) > 1000
                else enriched_context.markdown_content,
            )
            logger.debug("=" * 80)

        return {
            "data": enriched_context,
            "usage": usage_data,
        }

    except Exception as e:
        logger.error(
            f"EPFL enriched data context compilation failed: {e}",
            exc_info=True,
        )
        return {
            "data": None,
            "usage": None,
        }


async def assess_final_epfl_relationship(
    enriched_context: EnrichedDataContext,
) -> Dict[str, Any]:
    """
    Assess EPFL relationship from compiled enriched data.

    Stage 2 of the EPFL final assessment pipeline.

    Args:
        enriched_context: Compiled enriched data context from stage 1

    Returns:
        Dictionary with 'data' (EPFLAssessment) and 'usage' (dict with token info)
    """
    # Create context for the agent
    agent_context = {
        "enriched_context": enriched_context,
    }

    # Prepare the prompt
    prompt = get_epfl_assessment_prompt(enriched_context)

    # No tools for EPFL assessment agent
    tools = []

    try:
        # Run agent with fallback across multiple models
        result = await run_agent_with_fallback(
            EPFL_FINAL_CHECKER_CONFIGS,
            prompt,
            agent_context,
            EPFLAssessment,
            EPFL_ASSESSMENT_SYSTEM_PROMPT,
            tools,  # No tools for this agent
        )

        # Extract the output from PydanticAI result
        if hasattr(result, "output"):
            epfl_assessment = result.output
        else:
            epfl_assessment = result

        # Estimate tokens from prompt and response
        response_text = ""
        if hasattr(epfl_assessment, "model_dump_json"):
            response_text = epfl_assessment.model_dump_json()
        elif isinstance(epfl_assessment, dict):
            response_text = json.dumps(epfl_assessment)
        elif isinstance(epfl_assessment, str):
            response_text = epfl_assessment

        # Extract usage information from the result
        input_tokens = 0
        output_tokens = 0
        tool_calls_count = 0

        if hasattr(result, "usage"):
            usage = result.usage
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0
            tool_calls_count = getattr(usage, "tool_calls", 0) or 0

            # Fallback to details field for certain models
            if input_tokens == 0 and output_tokens == 0 and hasattr(usage, "details"):
                details = usage.details
                if isinstance(details, dict):
                    input_tokens = details.get("input_tokens", 0) or 0
                    output_tokens = details.get("output_tokens", 0) or 0

        # Calculate estimates with tool call support (always, for validation/fallback)
        estimated = estimate_tokens_with_tools(
            system_prompt=EPFL_ASSESSMENT_SYSTEM_PROMPT,
            user_prompt=prompt,
            response=response_text,
            tool_calls=tool_calls_count,
            tool_results_text=None,
        )

        # Use estimates as primary when API returns 0
        if input_tokens == 0 and output_tokens == 0:
            logger.warning(
                "API returned 0 tokens, using tiktoken estimates as primary counts",
            )
            input_tokens = estimated.get("input_tokens", 0)
            output_tokens = estimated.get("output_tokens", 0)

        usage_data = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_input_tokens": estimated.get("input_tokens", 0),
            "estimated_output_tokens": estimated.get("output_tokens", 0),
        }

        logger.info("Final EPFL relationship assessment completed successfully")

        return {
            "data": epfl_assessment,
            "usage": usage_data,
        }

    except Exception as e:
        logger.error(f"Final EPFL relationship assessment failed: {e}", exc_info=True)
        return {
            "data": None,
            "usage": None,
        }
