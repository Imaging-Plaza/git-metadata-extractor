"""
Organization context compiler agent - First stage of atomic agent pipeline.

This agent uses tools to gather comprehensive organization information
and compile it into a markdown document for the next agent.
"""

import json
import logging
from typing import Any, Dict

import httpx

from ...context.infoscience import (
    get_author_publications_tool,
    search_infoscience_labs_tool,
    search_infoscience_publications_tool,
)
from ...llm.model_config import load_model_config, validate_config
from ...utils.token_counter import (
    estimate_tokens_with_tools,
)
from ..agents_management import run_agent_with_fallback
from .models import CompiledContext

logger = logging.getLogger(__name__)

# Load model configurations for organization context compilation
ORGANIZATION_CONTEXT_COMPILER_CONFIGS = load_model_config(
    "run_organization_context_compiler",
)

# Validate configurations
for config in ORGANIZATION_CONTEXT_COMPILER_CONFIGS:
    if not validate_config(config):
        logger.error(
            f"Invalid configuration for organization context compiler: {config}",
        )
        raise ValueError("Invalid model configuration")

# System prompt for organization context compiler
ORGANIZATION_CONTEXT_COMPILER_SYSTEM_PROMPT = """
You are an expert at gathering and compiling comprehensive information about GitHub organizations and research institutions.

Your task is to:
1. Analyze the GitHub organization metadata provided (description, location, members, repositories, etc.)
2. Extract organization name variations and aliases from the metadata:
   - Full names (e.g., "Swiss Data Science Center")
   - Short names or acronyms (e.g., "SDSC")
   - GitHub handles (e.g., "sdsc-ordes")
   - Any alternative names mentioned in description, README, or metadata
3. Use available tools to search for additional information:
   - **Search Infoscience with MULTIPLE name variations**: Try the organization name, full name, acronyms, and any aliases found in the context
   - Search Infoscience for EPFL labs and organizational units (orgunit) matching the organization
   - Search Infoscience for publications related to the organization (try different name variations)
   - Get publications by organization members from Infoscience
   - Search the web for additional context about the organization (try different name variations)
4. Compile all information into a well-structured markdown document

**Search Strategy:**
- **ALWAYS try multiple name variations** when searching Infoscience and web:
  - Start with the GitHub organization name/handle
  - Try the full organization name if different (e.g., from description)
  - Try acronyms or short names (e.g., "SDSC" if full name is "Swiss Data Science Center")
  - Try name variations found in the metadata (description, README, etc.)
  - If one search returns no results, try another variation
- Example: For "sdsc-ordes", also search for "SDSC", "Swiss Data Science Center", "Swiss Data Science Center - ORDES", etc.

**Input Sources:**
- GitHub organization metadata: Description, location, members, repositories, README, etc.
- Tool results: Infoscience lab/orgunit searches, publication searches, web search results

**Output Format:**
Return ONLY a comprehensive markdown document (plain text, not JSON) that includes:
- Organization overview and mission
- Organization type and structure
- Affiliations and relationships (from GitHub, Infoscience)
- Research interests and disciplines (inferred from description, repositories, publications)
- Publications and research outputs (if found)
- Any other relevant information from GitHub metadata and tool searches

The compiled context should be thorough and well-organized for the next agent to extract structured metadata.

**IMPORTANT:** Return ONLY the markdown document as plain text. Do not wrap it in JSON, do not add any explanatory text, do not use code blocks. Just return the raw markdown text.
"""


async def search_web_tool(
    query: str,
) -> str:
    """
    Search DuckDuckGo for information about an organization.

    Args:
        query: The search query about an organization (e.g., "Swiss Data Science Center EPFL")

    Returns:
        Summary of search results from DuckDuckGo (JSON string)
    """
    logger.info(f"🔍 Agent tool called: search_web_tool('{query}')")

    try:
        # Simple DuckDuckGo search using their instant answer API
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": query,
                    "format": "json",
                    "no_html": "1",
                    "skip_disambig": "1",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

            results = []
            # Extract abstract if available
            if data.get("Abstract"):
                results.append(
                    {
                        "title": data.get("Heading", ""),
                        "abstract": data.get("Abstract", ""),
                        "url": data.get("AbstractURL", ""),
                    },
                )

            # Extract related topics
            for topic in data.get("RelatedTopics", [])[:5]:
                if isinstance(topic, dict) and "Text" in topic:
                    results.append(
                        {
                            "title": topic.get("Text", ""),
                            "url": topic.get("FirstURL", ""),
                        },
                    )

            logger.info(
                f"✓ Web search for '{query}' returned {len(results)} results",
            )
            return json.dumps(
                {
                    "query": query,
                    "results": results,
                },
                indent=2,
            )

    except Exception as e:
        logger.error(f"✗ Error searching web for '{query}': {e}")
        return json.dumps({"error": str(e)})


def get_organization_context_compiler_prompt(
    org_name: str,
    org_url: str,
    github_metadata: Dict[str, Any],
) -> str:
    """
    Generate prompt for organization context compiler agent.

    Args:
        org_name: GitHub organization name
        org_url: GitHub organization profile URL
        github_metadata: GitHub organization metadata dict

    Returns:
        Formatted prompt string
    """
    prompt = f"""Compile comprehensive information about this GitHub organization:

**Organization Name:** {org_name}
**GitHub Organization URL:** {org_url}

**GitHub Organization Metadata:**
{json.dumps(github_metadata, indent=2, default=str)}
"""

    prompt += """

Please:
1. Analyze the GitHub organization metadata provided
2. **Extract organization name variations** from the metadata:
   - Look for full names, acronyms, short names, or aliases in the description, README, or other metadata fields
   - Note any alternative names or variations that might be used in academic databases
3. Use available tools to search for additional information:
   - **Search Infoscience with MULTIPLE name variations**:
     * Start with the organization name: "{org_name}"
     * Also try the full organization name if different (e.g., from description field)
     * Try acronyms or short names (e.g., if description mentions "SDSC", also search for "SDSC")
     * Try any alternative names or variations found in the metadata
     * Search for EPFL labs/organizational units (orgunit search) with each variation
     * Search for publications related to the organization with each variation
   - Get publications by organization members from Infoscience if members are listed
   - Search the web for additional context about the organization (try different name variations)
4. Compile all information into a comprehensive markdown document

**Search Strategy:**
- If a search with one name returns no results, try another variation
- Example: For "{org_name}", if the description mentions "Swiss Data Science Center", also search for:
  * "Swiss Data Science Center"
  * "SDSC" (if that's the acronym)
  * Any other variations found in the metadata

Focus on gathering information that will help extract:
- Organization type and structure
- Research interests and scientific disciplines
- Affiliations and relationships with other organizations
- Publications and research outputs
- Mission and purpose

**IMPORTANT:** Return ONLY the markdown document as plain text. Do not wrap it in JSON, do not add any explanatory text, do not use code blocks. Just return the raw markdown text.
"""

    logger.debug(f"Organization context compiler prompt length: {len(prompt)} chars")
    return prompt


async def compile_organization_context(
    org_name: str,
    org_url: str,
    github_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compile organization context using tools to gather comprehensive information.

    Args:
        org_name: GitHub organization name
        org_url: GitHub organization profile URL
        github_metadata: GitHub organization metadata dict

    Returns:
        Dictionary with 'data' (CompiledContext) and 'usage' (dict with token info)
    """
    # Create context for the agent
    agent_context = {
        "org_name": org_name,
        "org_url": org_url,
        "github_metadata": github_metadata,
    }

    # Prepare the prompt
    prompt = get_organization_context_compiler_prompt(
        org_name,
        org_url,
        github_metadata,
    )

    # Define tools for the organization context compiler
    tools = [
        search_infoscience_labs_tool,
        search_infoscience_publications_tool,
        get_author_publications_tool,
        search_web_tool,
    ]

    try:
        # Run agent with fallback across multiple models
        # Use str as output type - agent returns markdown text
        result = await run_agent_with_fallback(
            ORGANIZATION_CONTEXT_COMPILER_CONFIGS,
            prompt,
            agent_context,
            str,  # Simple string output - just markdown text
            ORGANIZATION_CONTEXT_COMPILER_SYSTEM_PROMPT,
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
                repository_url=org_url,  # Use org_url as repository_url for consistency
                summary=None,
            )
        else:
            # Fallback if we get something unexpected
            compiled_context = CompiledContext(
                markdown_content=str(markdown_content),
                repository_url=org_url,
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
            system_prompt=ORGANIZATION_CONTEXT_COMPILER_SYSTEM_PROMPT,
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

        # Log compiled context size
        if hasattr(compiled_context, "markdown_content"):
            content_size = len(compiled_context.markdown_content)
        elif (
            isinstance(compiled_context, dict)
            and "markdown_content" in compiled_context
        ):
            content_size = len(compiled_context.get("markdown_content", ""))
        else:
            content_size = 0

        logger.info(
            f"Organization context compilation completed: {content_size:,} chars of markdown",
        )

        return {
            "data": compiled_context,
            "usage": usage_data,
        }

    except Exception as e:
        logger.error(f"Organization context compilation failed: {e}", exc_info=True)
        return {
            "data": None,
            "usage": None,
        }
