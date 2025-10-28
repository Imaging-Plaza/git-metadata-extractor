"""
User Analysis Agent
"""

import logging
from typing import Any, Dict, Optional

from ..context.infoscience import (
    get_author_publications_tool,
    search_infoscience_authors_tool,
)
from ..llm.model_config import (
    load_model_config,
    validate_config,
)
from .agents_management import cleanup_agents, run_agent_with_fallback
from .prompts import system_prompt_user_content
from .user_prompts import get_general_user_agent_prompt

# Setup logger first, before anything else
logger = logging.getLogger(__name__)


llm_analysis_configs = load_model_config("run_llm_analysis")

# Validate configurations
for config in llm_analysis_configs:
    if not validate_config(config):
        logger.error(f"Invalid configuration for LLM analysis: {config}")
        raise ValueError("Invalid model configuration")


async def llm_request_user_infos(
    username: str,
    user_data: Dict[str, Any],
    max_tokens: int = 20000,
) -> Optional[Dict[str, Any]]:  # TODO: Add here data class
    """
    Analyze GitHub user profile using PydanticAI with multi-provider support.

    Args:
        username: GitHub username to analyze
        user_data: User profile data from GitHub API
        output_format: Output format ("json" or "json-ld")
        max_tokens: Maximum tokens for input text

    Returns:
        Analysis result or None if failed
    """
    # Create context for the agent
    agent_context = {
        "username": username,
        "user_data": user_data,
    }

    # Prepare the prompt
    prompt = get_general_user_agent_prompt(username, user_data)

    try:
        # Define tools for the user agent
        tools = [
            search_infoscience_authors_tool,
            get_author_publications_tool,
        ]

        # Run agent with fallback across multiple models
        result = await run_agent_with_fallback(
            llm_analysis_configs,
            prompt,
            agent_context,
            Dict,  # Output type
            system_prompt_user_content,
            tools,
        )

        # Extract the output from PydanticAI result
        if hasattr(result, "output"):
            json_data = result.output
        else:
            json_data = result

        # Ensure it's a dictionary
        if hasattr(json_data, "model_dump"):
            json_data = json_data.model_dump()

        logger.info("Successfully received user analysis from agent")

        # Cleanup agents after successful completion
        await cleanup_agents()

        return json_data

    except Exception as e:
        logger.error(f"Error in user analysis: {e}")
        # Cleanup agents even on error
        await cleanup_agents()
        return None
