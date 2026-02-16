"""
User classifier agent - Classifies user discipline and position.

This agent takes compiled context and classifies the user's discipline(s)
and position(s) with justifications.
"""

import logging
from typing import Any, Dict

from ...llm.model_config import load_model_config, validate_config
from ...utils.token_counter import estimate_tokens_from_messages
from ..agents_management import run_agent_with_fallback
from .models import CompiledContext, UserClassification

logger = logging.getLogger(__name__)

# Load model configurations for user classification
USER_CLASSIFIER_CONFIGS = load_model_config("run_user_classifier")

# Validate configurations
for config in USER_CLASSIFIER_CONFIGS:
    if not validate_config(config):
        logger.error(f"Invalid configuration for user classifier: {config}")
        raise ValueError("Invalid model configuration")

# System prompt for user classifier
USER_CLASSIFIER_SYSTEM_PROMPT = """
You are an expert at classifying users by scientific discipline and professional position.

Your task is to:
1. Analyze the compiled user context provided
2. Determine relevant scientific disciplines (AT LEAST ONE REQUIRED) from the allowed values in the schema
3. Identify professional positions and roles
4. Provide clear justifications for each classification

**Important Guidelines:**
- discipline: REQUIRED - must select at least one valid discipline from the schema enum
- If multiple disciplines apply, list all relevant ones
- position: List professional positions, roles, or job titles (e.g., "Research Scientist", "PhD Student", "Professor", "Data Engineer")
- Provide evidence-based justifications referencing specific information from the user context
- Use the EXACT discipline names as specified in the JSON schema

**Note:** Valid discipline values are enforced by the JSON schema enum constraints.
"""


def get_user_classifier_prompt(compiled_context: CompiledContext) -> str:
    """
    Generate prompt for user classifier agent.

    Args:
        compiled_context: Compiled user context from context compiler

    Returns:
        Formatted prompt string
    """
    prompt = f"""Classify the following user:

**User Profile URL:** {compiled_context.repository_url}

**Compiled User Context:**
{compiled_context.markdown_content}

Please classify:
1. Scientific Disciplines (one or more relevant fields from the allowed list)
2. Professional Positions (roles, job titles, etc.)

Provide clear justifications for each classification based on the user context.
"""

    logger.debug(f"User classifier prompt length: {len(prompt)} chars")
    return prompt


async def classify_user_discipline_and_position(
    compiled_context: CompiledContext,
) -> Dict[str, Any]:
    """
    Classify user discipline and position from compiled context.

    Args:
        compiled_context: Compiled user context from context compiler

    Returns:
        Dictionary with:
        - data: UserClassification object
        - usage: Token usage statistics
    """
    logger.info("Starting user classification...")

    # Generate prompt
    prompt = get_user_classifier_prompt(compiled_context)

    # Prepare agent context (minimal - just pass compiled context)
    agent_context = {
        "user_url": compiled_context.repository_url,
        "compiled_context": compiled_context.markdown_content,
    }

    # No tools needed for classification
    tools = []

    logger.debug(f"Prompt length: {len(prompt)} characters")

    # Run agent with schema enforcement
    result = await run_agent_with_fallback(
        USER_CLASSIFIER_CONFIGS,
        prompt,
        agent_context,
        UserClassification,  # Schema enforcement
        USER_CLASSIFIER_SYSTEM_PROMPT,
        tools,
    )

    # Extract the classification from PydanticAI result
    # Check if result has an .output attribute (PydanticAI wrapper)
    if hasattr(result, "output"):
        classification_data = result.output
    else:
        classification_data = result

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
            f"User classification usage: {input_tokens} input, {output_tokens} output tokens",
        )
    else:
        logger.warning("No usage data available from agent")

    usage_data["input_tokens"] = input_tokens
    usage_data["output_tokens"] = output_tokens

    # Estimate tokens with tiktoken (serialize model properly)
    response_text = ""
    if hasattr(classification_data, "model_dump_json"):
        response_text = classification_data.model_dump_json()
    elif isinstance(classification_data, dict):
        import json as json_module

        response_text = json_module.dumps(classification_data)
    elif isinstance(classification_data, str):
        response_text = classification_data

    estimated = estimate_tokens_from_messages(
        system_prompt=USER_CLASSIFIER_SYSTEM_PROMPT,
        user_prompt=prompt,
        response=response_text,
    )
    usage_data["estimated_input_tokens"] = estimated.get("input_tokens", 0)
    usage_data["estimated_output_tokens"] = estimated.get("output_tokens", 0)

    logger.info(
        f"User classified with {len(classification_data.discipline)} discipline(s) "
        f"and {len(classification_data.position)} position(s)",
    )

    return {
        "data": classification_data,
        "usage": usage_data,
    }
