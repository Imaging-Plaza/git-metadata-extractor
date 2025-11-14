"""
Structured output agent - Second stage of atomic agent pipeline.

This agent takes compiled context and simplified schema instructions
to produce structured metadata output.
"""

import json
import logging
from pprint import pformat
from typing import Any, Dict, Optional

from ...llm.model_config import load_model_config, validate_config
from ...utils.token_counter import estimate_tokens_from_messages
from ..agents_management import run_agent_with_fallback
from .models import CompiledContext, SimplifiedRepositoryOutput

logger = logging.getLogger(__name__)

# Load model configurations for structured output
# Use a separate config that may disable tools
STRUCTURED_OUTPUT_CONFIGS = load_model_config("run_structured_output")

# Validate configurations
for config in STRUCTURED_OUTPUT_CONFIGS:
    if not validate_config(config):
        logger.error(f"Invalid configuration for structured output: {config}")
        raise ValueError("Invalid model configuration")

# System prompt for structured output agent
STRUCTURED_OUTPUT_SYSTEM_PROMPT = """
You are an expert at extracting structured metadata from repository information.

Your task is to:
1. Analyze the compiled repository context provided
2. Extract structured metadata according to the simplified schema provided
3. Output only the specified fields with correct data types

**Important Constraints:**
- Use ONLY primitive types: strings, numbers, lists, and dictionaries
- URLs must be strings (not HttpUrl objects)
- Dates must be ISO format strings (YYYY-MM-DD)
- Enums must be converted to strings
- Do not include fields not in the schema
- All required fields must be present

**Output Format:**
Return a JSON object matching the provided schema exactly.
"""


def get_structured_output_prompt(
    compiled_context: CompiledContext,
    schema: Dict[str, Any],
    example: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate prompt for structured output agent.

    Args:
        compiled_context: Compiled context from first agent
        schema: Simplified schema definition
        example: Optional example output

    Returns:
        Formatted prompt string
    """
    prompt = f"""Extract structured metadata from the compiled repository context.

**Compiled Context:**
{compiled_context.markdown_content}

**Repository URL:** {compiled_context.repository_url}

**Expected Output Schema:**
{json.dumps(schema, indent=2)}
"""

    if example:
        prompt += f"""

**Example Output (for reference):**
{json.dumps(example, indent=2)}
"""

    prompt += """

Please extract and return structured metadata matching the schema exactly.
Use only primitive types (strings, numbers, lists, dicts).
Convert all URLs, dates, and enums to strings.
"""

    return prompt


async def generate_structured_output(
    compiled_context: CompiledContext,
    schema: Dict[str, Any],
    example: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate structured output from compiled context.

    Args:
        compiled_context: Compiled context from context compiler
        schema: Simplified schema definition
        example: Optional example output

    Returns:
        Dictionary with 'data' (SimplifiedRepositoryOutput) and 'usage' (dict with token info)
    """
    # Create context for the agent
    agent_context = {
        "compiled_context": compiled_context,
        "schema": schema,
    }

    # Prepare the prompt
    prompt = get_structured_output_prompt(compiled_context, schema, example)

    # No tools for structured output agent
    tools = []

    try:
        # Run agent with fallback across multiple models
        result = await run_agent_with_fallback(
            STRUCTURED_OUTPUT_CONFIGS,
            prompt,
            agent_context,
            SimplifiedRepositoryOutput,
            STRUCTURED_OUTPUT_SYSTEM_PROMPT,
            tools,  # No tools for this agent
        )

        # Extract the output from PydanticAI result
        if hasattr(result, "output"):
            structured_output = result.output
        else:
            structured_output = result

        # Estimate tokens from prompt and response
        response_text = ""
        if hasattr(structured_output, "model_dump_json"):
            response_text = structured_output.model_dump_json()
        elif isinstance(structured_output, dict):
            import json as json_module

            response_text = json_module.dumps(structured_output)
        elif isinstance(structured_output, str):
            response_text = structured_output

        estimated = estimate_tokens_from_messages(
            system_prompt=STRUCTURED_OUTPUT_SYSTEM_PROMPT,
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

        logger.info("Structured output generation completed successfully")

        # Debug: Log the structured output JSON
        if hasattr(structured_output, "model_dump"):
            output_dict = structured_output.model_dump()
        elif isinstance(structured_output, dict):
            output_dict = structured_output
        else:
            output_dict = {"raw_output": str(structured_output)}

        logger.debug("=" * 80)
        logger.debug("STRUCTURED OUTPUT JSON (Second Agent Output):")
        logger.debug("=" * 80)
        logger.debug(pformat(output_dict, width=120, indent=2))
        logger.debug("=" * 80)

        return {
            "data": structured_output,
            "usage": usage_data,
        }

    except Exception as e:
        logger.error(f"Structured output generation failed: {e}", exc_info=True)
        return {
            "data": None,
            "usage": None,
        }
