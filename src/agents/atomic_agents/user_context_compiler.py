"""
User context compiler agent - First stage of atomic agent pipeline.

This agent uses tools to gather comprehensive user information
and compile it into a markdown document for the next agent.
"""

import json
import logging
from typing import Any, Dict, Optional

import httpx

from ...context.infoscience import (
    get_author_publications_tool,
    search_infoscience_authors_tool,
    search_infoscience_labs_tool,
)
from ...llm.model_config import load_model_config, validate_config
from ...utils.token_counter import (
    estimate_tokens_with_tools,
)
from ..agents_management import run_agent_with_fallback
from .models import CompiledContext

logger = logging.getLogger(__name__)

# Load model configurations for user context compilation
USER_CONTEXT_COMPILER_CONFIGS = load_model_config("run_user_context_compiler")

# Validate configurations
for config in USER_CONTEXT_COMPILER_CONFIGS:
    if not validate_config(config):
        logger.error(f"Invalid configuration for user context compiler: {config}")
        raise ValueError("Invalid model configuration")

# System prompt for user context compiler
USER_CONTEXT_COMPILER_SYSTEM_PROMPT = """
You are an expert at gathering and compiling comprehensive information about GitHub users and researchers.

Your task is to:
1. Analyze the GitHub user metadata provided (bio, README, ORCID, organizations, etc.)
2. Use available tools to search for additional information:
   - Search ORCID for author information and affiliations
   - Search Infoscience for EPFL authors and researchers (persona)
   - Search Infoscience for EPFL labs and organizational units (orgunit)
   - Search the web for additional context about the user
   - Get publications by the user from Infoscience
3. Compile all information into a well-structured markdown document

**Input Sources:**
- GitHub user metadata: Bio, README, ORCID, organizations, location, company, etc.
- Tool results: ORCID records, Infoscience author/lab searches, web search results, publications

**Output Format:**
Return ONLY a comprehensive markdown document (plain text, not JSON) that includes:
- User overview and professional background
- Affiliations and organizations (from GitHub, ORCID, Infoscience)
- Research interests and disciplines (inferred from bio, publications, affiliations)
- Professional positions and roles
- Publications and research outputs (if found)
- Any other relevant information from GitHub metadata and tool searches

The compiled context should be thorough and well-organized for the next agent to extract structured metadata.

**IMPORTANT:** Return ONLY the markdown document as plain text. Do not wrap it in JSON, do not add any explanatory text, do not use code blocks. Just return the raw markdown text.
"""


async def search_orcid_tool(
    author_name: str,
    email: Optional[str] = None,
) -> str:
    """
    Search the ORCID API for author information.

    Args:
        author_name: The author's name to search for
        email: Optional email address to help narrow the search

    Returns:
        JSON string with ORCID search results including ORCID IDs, names, and affiliations
    """
    logger.info(f"🔍 Agent tool called: search_orcid_tool('{author_name}', '{email}')")

    try:
        # Build search query
        query_parts = []

        # Add name to query
        if author_name:
            # Try to parse first and last name
            name_parts = author_name.strip().split()
            if len(name_parts) >= 2:
                given_name = name_parts[0]
                family_name = " ".join(name_parts[1:])
                query_parts.append(f"given-names:{given_name}")
                query_parts.append(f"family-name:{family_name}")
            else:
                query_parts.append(f"family-name:{author_name}")

        # Add email to query if provided
        if email:
            query_parts.append(f"email:{email}")

        if not query_parts:
            return json.dumps({"error": "No search criteria provided"})

        query = " AND ".join(query_parts)

        async with httpx.AsyncClient() as client:
            headers = {
                "Accept": "application/json",
            }
            response = await client.get(
                "https://pub.orcid.org/v3.0/search/",
                params={"q": query},
                headers=headers,
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

            # Extract relevant information from results
            results = []
            num_found = data.get("num-found", 0)

            if num_found == 0:
                logger.info(f"⚠ No ORCID records found for '{author_name}'")
                return json.dumps({"query": query, "results": [], "num_found": 0})

            for result in data.get("result", [])[:5]:  # Top 5 results
                orcid_id = result.get("orcid-identifier", {}).get("path")

                # Get basic info from search result
                person_info = {
                    "orcid_id": f"https://orcid.org/{orcid_id}" if orcid_id else None,
                    "given_names": result.get("given-names"),
                    "family_name": result.get("family-name"),
                    "credit_name": result.get("credit-name"),
                }

                # Note: Full affiliation details require a separate API call
                if orcid_id:
                    person_info[
                        "note"
                    ] = f"Full affiliation details available at https://pub.orcid.org/v3.0/{orcid_id}/employments"

                results.append(person_info)

            logger.info(
                f"✓ ORCID search for '{author_name}' returned {len(results)} results",
            )
            return json.dumps(
                {
                    "query": query,
                    "results": results,
                    "num_found": num_found,
                },
                indent=2,
            )

    except Exception as e:
        logger.error(f"✗ Error searching ORCID for '{author_name}': {e}")
        return json.dumps({"error": str(e)})


async def search_web_tool(
    query: str,
) -> str:
    """
    Search DuckDuckGo for information about a person.

    Args:
        query: The search query about a person (e.g., "John Smith EPFL researcher")

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


def get_user_context_compiler_prompt(
    username: str,
    user_url: str,
    github_metadata: Dict[str, Any],
) -> str:
    """
    Generate prompt for user context compiler agent.

    Args:
        username: GitHub username
        user_url: GitHub user profile URL
        github_metadata: GitHub user metadata dict

    Returns:
        Formatted prompt string
    """
    prompt = f"""Compile comprehensive information about this GitHub user:

**GitHub Username:** {username}
**GitHub Profile URL:** {user_url}

**GitHub User Metadata:**
{json.dumps(github_metadata, indent=2, default=str)}
"""

    prompt += """

Please:
1. Analyze the GitHub user metadata provided
2. Use available tools to search for additional information:
   - Search ORCID if the user has an ORCID ID or if you need to find their ORCID profile
   - Search Infoscience for EPFL authors/researchers (persona search) using the user's name
   - Search Infoscience for EPFL labs/organizational units (orgunit search) if organizations are mentioned
   - Search the web for additional context about the user
   - Get publications by the user from Infoscience if found
3. Compile all information into a comprehensive markdown document

Focus on gathering information that will help extract:
- User's professional background and affiliations
- Research interests and scientific disciplines
- Professional positions and roles
- Organizations the user is affiliated with
- Publications and research outputs

**IMPORTANT:** Return ONLY the markdown document as plain text. Do not wrap it in JSON, do not add any explanatory text, do not use code blocks. Just return the raw markdown text.
"""

    logger.debug(f"User context compiler prompt length: {len(prompt)} chars")
    return prompt


async def compile_user_context(
    username: str,
    user_url: str,
    github_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compile user context using tools to gather comprehensive information.

    Args:
        username: GitHub username
        user_url: GitHub user profile URL
        github_metadata: GitHub user metadata dict

    Returns:
        Dictionary with 'data' (CompiledContext) and 'usage' (dict with token info)
    """
    # Create context for the agent
    agent_context = {
        "username": username,
        "user_url": user_url,
        "github_metadata": github_metadata,
    }

    # Prepare the prompt
    prompt = get_user_context_compiler_prompt(username, user_url, github_metadata)

    # Define tools for the user context compiler
    tools = [
        search_orcid_tool,
        search_infoscience_authors_tool,
        search_infoscience_labs_tool,
        get_author_publications_tool,
        search_web_tool,
    ]

    try:
        # Run agent with fallback across multiple models
        # Use str as output type - agent returns markdown text
        result = await run_agent_with_fallback(
            USER_CONTEXT_COMPILER_CONFIGS,
            prompt,
            agent_context,
            str,  # Simple string output - just markdown text
            USER_CONTEXT_COMPILER_SYSTEM_PROMPT,
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
                repository_url=user_url,  # Use user_url as repository_url for consistency
                summary=None,
            )
        else:
            # Fallback if we get something unexpected
            compiled_context = CompiledContext(
                markdown_content=str(markdown_content),
                repository_url=user_url,
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
            system_prompt=USER_CONTEXT_COMPILER_SYSTEM_PROMPT,
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
            f"User context compilation completed: {content_size:,} chars of markdown",
        )

        return {
            "data": compiled_context,
            "usage": usage_data,
        }

    except Exception as e:
        logger.error(f"User context compilation failed: {e}", exc_info=True)
        return {
            "data": None,
            "usage": None,
        }
