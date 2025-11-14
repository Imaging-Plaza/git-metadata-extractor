"""
EPFL checker agent - Third stage of atomic agent pipeline.

This agent assesses EPFL relationship from compiled context only,
without access to tools.
"""

import logging
from typing import Any, Dict

from ...llm.model_config import load_model_config, validate_config
from ...utils.token_counter import estimate_tokens_from_messages
from ..agents_management import run_agent_with_fallback
from .models import CompiledContext, EPFLAssessment

logger = logging.getLogger(__name__)

# Load model configurations for EPFL assessment
EPFL_CHECKER_CONFIGS = load_model_config("run_epfl_checker")

# Validate configurations
for config in EPFL_CHECKER_CONFIGS:
    if not validate_config(config):
        logger.error(f"Invalid configuration for EPFL checker: {config}")
        raise ValueError("Invalid model configuration")

# System prompt for EPFL checker agent
EPFL_CHECKER_SYSTEM_PROMPT = """
You are an expert at assessing relationships between software repositories and EPFL (École Polytechnique Fédérale de Lausanne).

Your task is to:
1. Analyze the compiled repository context
2. Identify evidence of EPFL relationship from the provided context only
3. Provide a confidence score (0.0 to 1.0) and detailed justification

**Evidence to Look For (in the provided context only):**
- Authors with @epfl.ch email addresses
- Mentions of EPFL, SDSC (Swiss Data Science Center), or EPFL labs in documentation
- EPFL affiliations in author information
- Related organizations that include EPFL
- Repository hosted under EPFL GitHub organizations

**Note:** Do NOT search for additional information. Only use evidence present in the compiled context provided.

**Confidence Scoring:**
- 0.9-1.0: Strong evidence (multiple high-quality sources)
- 0.7-0.89: Good evidence (verified sources)
- 0.5-0.69: Moderate evidence (partial information)
- 0.3-0.49: Weak evidence (limited information)
- 0.0-0.29: Very weak/speculative evidence

**Output:**
- relatedToEPFL: Boolean (true if confidence >= 0.5)
- relatedToEPFLConfidence: Float (0.0 to 1.0)
- relatedToEPFLJustification: String (detailed explanation of evidence)
"""


def get_epfl_checker_prompt(compiled_context: CompiledContext) -> str:
    """
    Generate prompt for EPFL checker agent.

    Args:
        compiled_context: Compiled context from first agent

    Returns:
        Formatted prompt string
    """
    prompt = f"""Assess the EPFL relationship for this repository.

**Compiled Context:**
{compiled_context.markdown_content}

**Repository URL:** {compiled_context.repository_url}

Please analyze the context and determine:
1. Is this repository related to EPFL?
2. What is your confidence level (0.0 to 1.0)?
3. What evidence supports your assessment?

Provide a detailed justification listing all evidence found.
"""

    return prompt


async def check_epfl_relationship(compiled_context: CompiledContext) -> Dict[str, Any]:
    """
    Check EPFL relationship from compiled context.

    Args:
        compiled_context: Compiled context from context compiler

    Returns:
        Dictionary with 'data' (EPFLAssessment) and 'usage' (dict with token info)
    """
    # Create context for the agent
    agent_context = {
        "compiled_context": compiled_context,
    }

    # Prepare the prompt
    prompt = get_epfl_checker_prompt(compiled_context)

    # No tools for EPFL checker agent
    tools = []

    try:
        # Run agent with fallback across multiple models
        result = await run_agent_with_fallback(
            EPFL_CHECKER_CONFIGS,
            prompt,
            agent_context,
            EPFLAssessment,
            EPFL_CHECKER_SYSTEM_PROMPT,
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
            import json as json_module

            response_text = json_module.dumps(epfl_assessment)
        elif isinstance(epfl_assessment, str):
            response_text = epfl_assessment

        estimated = estimate_tokens_from_messages(
            system_prompt=EPFL_CHECKER_SYSTEM_PROMPT,
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

        logger.info("EPFL relationship assessment completed successfully")

        return {
            "data": epfl_assessment,
            "usage": usage_data,
        }

    except Exception as e:
        logger.error(f"EPFL relationship assessment failed: {e}", exc_info=True)
        return {
            "data": None,
            "usage": None,
        }
