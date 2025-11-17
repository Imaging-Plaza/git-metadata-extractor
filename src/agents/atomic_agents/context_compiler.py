"""
Context compiler agent - First stage of atomic agent pipeline.

This agent uses tools to gather comprehensive repository information
and compile it into a markdown document for the next agent.
"""

import logging
from typing import Any, Dict, Optional

# Tools removed - context compiler only uses repository content and GIMIE data
# from ...context.infoscience import (
#     get_author_publications_tool,
#     search_infoscience_publications_tool,
# )
from ...llm.model_config import load_model_config, validate_config
from ...utils.token_counter import estimate_tokens_from_messages
from ..agents_management import run_agent_with_fallback
from .models import CompiledContext

logger = logging.getLogger(__name__)

# Load model configurations for context compilation
CONTEXT_COMPILER_CONFIGS = load_model_config("run_context_compiler")

# Validate configurations
for config in CONTEXT_COMPILER_CONFIGS:
    if not validate_config(config):
        logger.error(f"Invalid configuration for context compiler: {config}")
        raise ValueError("Invalid model configuration")

# System prompt for context compiler
CONTEXT_COMPILER_SYSTEM_PROMPT = """
You are an expert at gathering and compiling comprehensive information about software repositories.

Your task is to:
1. Analyze the repository content provided (code, README, documentation, etc.)
2. Analyze the GIMIE metadata provided (if available)
3. Compile all information into a well-structured markdown document

**Input Sources:**
- Repository content: Code, README, documentation files, and other repository files
- GIMIE metadata: Structured metadata extracted from the Git provider (GitHub/GitLab)

**Output Format:**
Return ONLY a comprehensive markdown document (plain text, not JSON) that includes:
- Repository overview and description
- Key features and functionality
- Authors and contributors (with affiliations if mentioned in repository content or GIMIE data)
- Technologies and dependencies
- License information
- Any other relevant information from the repository content and GIMIE metadata

The compiled context should be thorough and well-organized for the next agent to extract structured metadata.
Do NOT search for additional information outside of what is provided in the repository content and GIMIE metadata.

**IMPORTANT:** Return ONLY the markdown document as plain text. Do not wrap it in JSON, do not add any explanatory text, do not use code blocks. Just return the raw markdown text.
"""


def get_context_compiler_prompt(
    repo_url: str,
    repository_content: str,
    gimie_data: Optional[str] = None,
) -> str:
    """
    Generate prompt for context compiler agent.

    Args:
        repo_url: Repository URL
        repository_content: Extracted repository content (markdown)
        gimie_data: Optional GIMIE metadata

    Returns:
        Formatted prompt string
    """
    prompt = f"""Compile comprehensive information about this repository:

**Repository URL:** {repo_url}

**Repository Content:**
{repository_content}
"""

    if gimie_data:
        # Parse GIMIE data to extract structured authors/orgs if available
        try:
            import json as json_module

            gimie_dict = json_module.loads(gimie_data)

            # Extract structured authors and organizations if available
            extracted_authors = gimie_dict.get("extracted_authors", [])
            extracted_orgs = gimie_dict.get("extracted_organizations", [])

            prompt += f"""

**GIMIE Metadata (extracted from Git provider):**
{gimie_data}
"""

            # Add structured authors/orgs section if available
            if extracted_authors or extracted_orgs:
                prompt += f"""

**Pre-extracted Authors and Organizations from GIMIE:**

**Authors ({len(extracted_authors)}):**
{json_module.dumps(extracted_authors, indent=2)}

**Organizations ({len(extracted_orgs)}):**
{json_module.dumps(extracted_orgs, indent=2)}

**Important:** These authors and organizations have been pre-extracted from GIMIE with their affiliations already resolved. Use this structured data when identifying authors and organizations in your compiled context. The affiliations field in authors may contain organization objects (with id, legalName, etc.) or organization name strings.
"""
        except Exception as e:
            # If parsing fails, just include raw GIMIE data
            logger.warning(f"Failed to parse GIMIE data for structured extraction: {e}")
            prompt += f"""

**GIMIE Metadata (extracted from Git provider):**
{gimie_data}
"""
        logger.debug("GIMIE data included in context compiler prompt")
    else:
        logger.debug("No GIMIE data to include in context compiler prompt")

    prompt += """

Please:
1. Analyze the repository content provided
2. Analyze the GIMIE metadata provided (if available)
3. Compile all information into a comprehensive markdown document

Focus on extracting and organizing information from the provided sources to help extract structured metadata in the next step.
Do NOT search for additional information - only use what is provided in the repository content and GIMIE metadata.

**IMPORTANT:** Return ONLY the markdown document as plain text. Do not wrap it in JSON, do not add any explanatory text, do not use code blocks. Just return the raw markdown text.
"""

    logger.debug(f"Context compiler prompt length: {len(prompt)} chars")
    return prompt


async def compile_repository_context(
    repo_url: str,
    repository_content: str,
    gimie_data: Optional[str] = None,
    git_authors: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Compile repository context using tools to gather comprehensive information.

    Args:
        repo_url: Repository URL
        repository_content: Extracted repository content
        gimie_data: Optional GIMIE metadata
        git_authors: Optional list of git authors

    Returns:
        Dictionary with 'data' (CompiledContext) and 'usage' (dict with token info)
    """
    # Create context for the agent
    agent_context = {
        "repo_url": repo_url,
        "repository_content": repository_content,
        "gimie_data": gimie_data,
        "git_authors": git_authors or [],
    }

    # Prepare the prompt
    prompt = get_context_compiler_prompt(repo_url, repository_content, gimie_data)

    # No tools for context compilation - only use repository content and GIMIE data
    tools = []

    try:
        # Run agent with fallback across multiple models
        # Use str as output type - agent returns markdown text
        result = await run_agent_with_fallback(
            CONTEXT_COMPILER_CONFIGS,
            prompt,
            agent_context,
            str,  # Simple string output - just markdown text
            CONTEXT_COMPILER_SYSTEM_PROMPT,
            tools,
        )

        # Extract the markdown string from PydanticAI result
        if hasattr(result, "output"):
            markdown_content = result.output
        else:
            markdown_content = result

        # Convert string to CompiledContext
        if isinstance(markdown_content, str):
            compiled_context = CompiledContext(
                markdown_content=markdown_content,
                repository_url=repo_url,
                summary=None,
            )
        else:
            # Fallback if we get something unexpected
            compiled_context = CompiledContext(
                markdown_content=str(markdown_content),
                repository_url=repo_url,
                summary=None,
            )

        # Estimate tokens from prompt and response
        response_text = ""
        if hasattr(compiled_context, "model_dump_json"):
            response_text = compiled_context.model_dump_json()
        elif isinstance(compiled_context, dict):
            import json as json_module

            response_text = json_module.dumps(compiled_context)
        elif isinstance(compiled_context, str):
            response_text = compiled_context

        estimated = estimate_tokens_from_messages(
            system_prompt=CONTEXT_COMPILER_SYSTEM_PROMPT,
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

        logger.info("Context compilation completed successfully")

        # Debug: Log the compiled markdown content
        if hasattr(compiled_context, "markdown_content"):
            logger.debug("=" * 80)
            logger.debug("COMPILED CONTEXT MARKDOWN (First Agent Output):")
            logger.debug("=" * 80)
            logger.debug(compiled_context.markdown_content)
            logger.debug("=" * 80)
        elif (
            isinstance(compiled_context, dict)
            and "markdown_content" in compiled_context
        ):
            logger.debug("=" * 80)
            logger.debug("COMPILED CONTEXT MARKDOWN (First Agent Output):")
            logger.debug("=" * 80)
            logger.debug(compiled_context.get("markdown_content", ""))
            logger.debug("=" * 80)

        return {
            "data": compiled_context,
            "usage": usage_data,
        }

    except Exception as e:
        logger.error(f"Context compilation failed: {e}", exc_info=True)
        return {
            "data": None,
            "usage": None,
        }
