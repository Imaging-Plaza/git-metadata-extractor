"""
Organization Analysis Agent
"""

import logging
from typing import Any, Dict

from ..context.infoscience import (
    get_author_publications_tool,
    search_infoscience_labs_tool,
    search_infoscience_publications_tool,
)
from ..data_models import OrganizationLLMAnalysisResult
from ..llm.model_config import (
    load_model_config,
    validate_config,
)
from .agents_management import cleanup_agents, run_agent_with_fallback
from .organization_prompts import (
    get_general_organization_agent_prompt,
    system_prompt_organization_content,
)

# Setup logger first, before anything else
logger = logging.getLogger(__name__)


llm_analysis_configs = load_model_config("run_llm_analysis")

# Validate configurations
for config in llm_analysis_configs:
    if not validate_config(config):
        logger.error(f"Invalid configuration for LLM analysis: {config}")
        raise ValueError("Invalid model configuration")


async def llm_request_org_infos(
    org_name: str,
    org_data: Dict[str, Any],
    max_tokens: int = 20000,
) -> Dict[str, Any]:
    """
    Analyze GitHub organization profile using PydanticAI with multi-provider support.

    Args:
        org_name: GitHub organization name to analyze
        org_data: Organization profile data from GitHub API
        max_tokens: Maximum tokens for input text

    Returns:
        Dictionary with 'data' (dict) and 'usage' (dict with token info) keys,
        or {'data': None, 'usage': None} if failed
    """
    # Create context for the agent
    agent_context = {
        "org_name": org_name,
        "org_data": org_data,
    }

    # Prepare the prompt
    prompt = get_general_organization_agent_prompt(org_name, org_data)

    try:
        # Define tools for the organization agent
        tools = [
            search_infoscience_labs_tool,
            search_infoscience_publications_tool,
            get_author_publications_tool,
        ]

        # Run agent with fallback across multiple models
        result = await run_agent_with_fallback(
            llm_analysis_configs,
            prompt,
            agent_context,
            OrganizationLLMAnalysisResult,  # Output type - enforces schema!
            system_prompt_organization_content,
            tools,
        )

        # Extract the output from PydanticAI result
        if hasattr(result, "output"):
            json_data = result.output
        else:
            json_data = result

        # Convert to dictionary for compatibility
        if hasattr(json_data, "model_dump"):
            json_data = json_data.model_dump()
        elif isinstance(json_data, OrganizationLLMAnalysisResult):
            json_data = json_data.model_dump()

        logger.info("Successfully received organization analysis from agent")
        logger.info(f"Organization analysis fields populated: {list(json_data.keys())}")

        # Cleanup agents after successful completion
        await cleanup_agents()

        # Return in the same format as repository agent
        return {
            "data": json_data,
            "usage": None,  # TODO: Add token usage tracking like repository agent
        }

    except Exception as e:
        logger.error(f"Error in organization analysis: {e}")
        # Cleanup agents even on error
        await cleanup_agents()
        return {"data": None, "usage": None}
