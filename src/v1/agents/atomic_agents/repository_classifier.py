"""
Repository classifier agent - Classifies repository type and discipline.

This agent takes compiled context and classifies the repository's type
and scientific discipline(s) with justifications.
"""

import logging
from typing import Any, Dict

from ...llm.model_config import load_model_config, validate_config
from ...utils.token_counter import estimate_tokens_from_messages
from ..agents_management import run_agent_with_fallback
from .models import CompiledContext, RepositoryClassification

logger = logging.getLogger(__name__)

# Load model configurations for repository classification
REPOSITORY_CLASSIFIER_CONFIGS = load_model_config("run_repository_classifier")

# Validate configurations
for config in REPOSITORY_CLASSIFIER_CONFIGS:
    if not validate_config(config):
        logger.error(f"Invalid configuration for repository classifier: {config}")
        raise ValueError("Invalid model configuration")

# System prompt for repository classifier
REPOSITORY_CLASSIFIER_SYSTEM_PROMPT = """
You are an expert at classifying software repositories by type and scientific discipline.

Your task is to:
1. Analyze the compiled repository context provided
2. Determine the primary repository type from the allowed values in the schema
3. Identify relevant scientific disciplines (AT LEAST ONE REQUIRED) from the allowed values in the schema
4. Provide clear justifications for each classification

**Important Guidelines:**
- repositoryType: Choose from the valid types in the schema (software, educational resource, documentation, data, webpage, other)
- discipline: REQUIRED - must select at least one valid discipline from the schema enum
- If multiple disciplines apply, list all relevant ones
- Provide evidence-based justifications referencing specific repository content
- Use the EXACT discipline names as specified in the JSON schema

**Repository Type Descriptions:**
- software: Code for applications, tools, libraries, frameworks
- educational resource: Educational materials, courses, tutorials
- documentation: Primarily documentation, guides
- data: Data collections, databases, datasets
- webpage: Static websites, landing pages, personal pages
- other: Anything that doesn't fit the above categories

**Note:** Valid discipline and repository type values are enforced by the JSON schema enum constraints.
"""


def get_repository_classifier_prompt(compiled_context: CompiledContext) -> str:
    """
    Generate prompt for repository classifier agent.

    Args:
        compiled_context: Compiled repository context from context compiler

    Returns:
        Formatted prompt string
    """
    prompt = f"""Classify the following repository:

**Repository URL:** {compiled_context.repository_url}

**Compiled Repository Context:**
{compiled_context.markdown_content}

Please classify:
1. Repository Type (software, dataset, model, documentation, or other)
2. Scientific Disciplines (one or more relevant fields)

Provide clear justifications for each classification based on the repository content.
"""

    logger.debug(f"Repository classifier prompt length: {len(prompt)} chars")
    return prompt


async def classify_repository_type_and_discipline(
    compiled_context: CompiledContext,
) -> Dict[str, Any]:
    """
    Classify repository type and discipline from compiled context.

    Args:
        compiled_context: Compiled repository context from context compiler

    Returns:
        Dictionary with:
        - data: RepositoryClassification object
        - usage: Token usage statistics
    """
    logger.info("Starting repository classification...")

    # Generate prompt
    prompt = get_repository_classifier_prompt(compiled_context)

    # Prepare agent context (minimal - just pass compiled context)
    agent_context = {
        "repository_url": compiled_context.repository_url,
        "compiled_context": compiled_context.markdown_content,
    }

    # No tools needed for classification
    tools = []

    logger.debug(f"Prompt length: {len(prompt)} characters")

    # Run agent with schema enforcement
    result = await run_agent_with_fallback(
        REPOSITORY_CLASSIFIER_CONFIGS,
        prompt,
        agent_context,
        RepositoryClassification,  # Schema enforcement
        REPOSITORY_CLASSIFIER_SYSTEM_PROMPT,
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
            f"Repository classification usage: {input_tokens} input, {output_tokens} output tokens",
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
        system_prompt=REPOSITORY_CLASSIFIER_SYSTEM_PROMPT,
        user_prompt=prompt,
        response=response_text,
    )
    usage_data["estimated_input_tokens"] = estimated.get("input_tokens", 0)
    usage_data["estimated_output_tokens"] = estimated.get("output_tokens", 0)

    logger.info(
        f"Repository classified as: {classification_data.repositoryType} "
        f"with {len(classification_data.discipline)} discipline(s)",
    )

    return {
        "data": classification_data,
        "usage": usage_data,
    }
