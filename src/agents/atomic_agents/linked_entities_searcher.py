"""
Linked entities searcher agent - Academic catalog search using atomic agent pipeline.

This module implements a two-stage pipeline for searching academic catalogs:
1. Context compiler with tools: Searches Infoscience for repository and authors
2. Structured output: Organizes search results into structured format
"""

import json
import logging
from typing import Any, Dict, List

from ...context.infoscience import (
    search_infoscience_publications_tool,
)
from ...data_models.conversion import create_simplified_model
from ...data_models.linked_entities import linkedEntitiesEnrichmentResult
from ...llm.model_config import load_model_config, validate_config
from ...utils.token_counter import (
    estimate_tokens_with_tools,
)
from ..agents_management import run_agent_with_fallback
from .models import LinkedEntitiesContext

logger = logging.getLogger(__name__)

# Load model configurations for linked entities search
LINKED_ENTITIES_SEARCHER_CONFIGS = load_model_config("run_linked_entities_searcher")

# Generate simplified model dynamically from linkedEntitiesEnrichmentResult
# Only include fields needed for LLM output (repository_relations only)
# Cache it at module level to avoid regenerating on every call
LINKED_ENTITIES_FIELDS = [
    "repository_relations",
    # Note: author_relations and organization_relations are handled in optional enrichment
]

(
    _SIMPLIFIED_LINKED_ENTITIES_MODEL,
    _LINKED_ENTITIES_UNION_METADATA,
) = create_simplified_model(
    linkedEntitiesEnrichmentResult,
    field_filter=LINKED_ENTITIES_FIELDS,
)

# Validate configurations
for config in LINKED_ENTITIES_SEARCHER_CONFIGS:
    if not validate_config(config):
        logger.error(f"Invalid configuration for linked entities searcher: {config}")
        raise ValueError("Invalid model configuration")

# System prompt for search stage (with tools)
LINKED_ENTITIES_SEARCH_SYSTEM_PROMPT = """
You are an expert at searching academic catalogs to find publications related to software repositories.

Your task is to:
1. Search Infoscience (EPFL's research repository) for the repository/tool name
2. Find relevant publications and related entities
3. Compile search results into a comprehensive markdown document

**Available Tools:**
- search_infoscience_publications_tool(query, max_results): Search for publications by repository/tool name

**Search Strategy:**
1. Search for the repository/tool name to find publications about or using it (max 5 results)
2. Be strategic - ONE search per repository, avoid repetition
3. If a search returns 0 results, STOP searching (it's not in Infoscience)

**IMPORTANT CONSTRAINTS:**
- Maximum 5 results per search
- ONE search for the repository (don't try variations or search again)
- Cache automatically stores results (including empty results)
- Accept when information is not found rather than keep searching

**Output Format:**
Return ONLY a comprehensive markdown document (plain text, not JSON) with:
- Repository/Tool Search Results section
- Clear indication when no results are found

**IMPORTANT:** Return ONLY the markdown document as plain text. Do not wrap it in JSON, do not add explanatory text, do not use code blocks. Just return the raw markdown text.
"""

# System prompt for structuring stage (no tools)
LINKED_ENTITIES_STRUCTURE_SYSTEM_PROMPT = """
You are an expert at organizing academic catalog search results into structured data.

Your task is to:
1. Analyze the search results markdown provided
2. Extract and organize results for repository-level entities
3. Structure the data according to the provided schema

**Important:**
- Use ONLY primitive types: strings, numbers, lists, and dictionaries
- URLs must be strings (not HttpUrl objects)
- Do not include fields not in the schema
- Organize results into repository_relations (publications about the repository/tool)
- **catalogType** MUST be one of: "infoscience", "openalex", or "epfl_graph" (for Infoscience results, use "infoscience")
- **entityType** MUST be one of: "publication", "person", or "orgunit"

**CRITICAL: Entity Field Handling:**
The entity field is a Union that gets split into THREE separate fields based on entity type:
- **entityInfosciencePublication**: Populate ONLY when entityType is "publication" - leave the other two fields EMPTY/OMITTED
- **entityInfoscienceAuthor**: Populate ONLY when entityType is "person" - leave the other two fields EMPTY/OMITTED
- **entityInfoscienceLab**: Populate ONLY when entityType is "orgunit" - leave the other two fields EMPTY/OMITTED

**CRITICAL RULE: Only populate ONE of these three fields per relation - the one matching the entityType!**
- If entityType="publication", ONLY populate entityInfosciencePublication (do NOT populate entityInfoscienceAuthor or entityInfoscienceLab)
- If entityType="person", ONLY populate entityInfoscienceAuthor (do NOT populate entityInfosciencePublication or entityInfoscienceLab)
- If entityType="orgunit", ONLY populate entityInfoscienceLab (do NOT populate entityInfosciencePublication or entityInfoscienceAuthor)

**List Fields:**
- For list fields like "subjects", "authors", "keywords": Use empty array [] if no data, NEVER use null/None

For each entity type, include these fields:

**entityInfosciencePublication (when entityType="publication"):**
  - title: Publication title
  - authors: List of author names
  - url: Full Infoscience URL
  - uuid: Entity UUID
  - publication_date: Publication date (if available)

**entityInfoscienceAuthor (when entityType="person"):**
  - name: Person's full name
  - profile_url: Full Infoscience profile URL
  - uuid: Entity UUID
  - email: Email address (if available)
  - orcid: ORCID identifier (if available)
  - affiliation: Primary affiliation/lab (if available)

**entityInfoscienceLab (when entityType="orgunit"):**
  - name: Lab/organization name
  - url: Full Infoscience URL
  - uuid: Entity UUID

**Example for a publication from Infoscience:**
```json
{
  "catalogType": "infoscience",
  "entityType": "publication",
  "entityInfosciencePublication": {
    "type": "InfosciencePublication",
    "title": "DeepLabCut: markerless pose estimation",
    "authors": ["Alexander Mathis", "Mackenzie Mathis"],
    "url": "https://infoscience.epfl.ch/entities/publication/12345",
    "uuid": "12345-67890",
    "publication_date": "2020-01-15",
    "subjects": ["Computer Science", "Machine Learning"]
  },
  "confidence": 0.9,
  "justification": "Found publication about the repository in Infoscience"
}
```
Note: Only entityInfosciencePublication is populated. Do NOT include entityInfoscienceAuthor or entityInfoscienceLab fields at all.

**Output Format:**
Return a JSON object matching the provided schema exactly.
"""


def get_linked_entities_search_prompt(
    repository_name: str,
    author_names: List[str],  # Kept for backward compatibility but unused
) -> str:
    """
    Generate prompt for linked entities search.

    Args:
        repository_name: Repository or tool name to search for
        author_names: Unused (kept for backward compatibility)

    Returns:
        Formatted prompt string
    """
    prompt = f"""Search academic catalogs for this repository.

**Repository/Tool Name:** {repository_name}

Please:
1. Search for the repository/tool name in publications (max 5 results)
2. Compile all search results into a well-organized markdown document

Use the provided tools strategically - ONE search for the repository, max 5 results.

**IMPORTANT:** Return ONLY the markdown document as plain text. Do not wrap it in JSON, do not add explanatory text, do not use code blocks. Just return the raw markdown text.
"""

    return prompt


def get_linked_entities_structure_prompt(
    search_context: LinkedEntitiesContext,
    schema: Dict[str, Any],
) -> str:
    """
    Generate prompt for structuring linked entities results.

    Args:
        search_context: Compiled search results context
        schema: Simplified schema definition

    Returns:
        Formatted prompt string
    """
    prompt = f"""Organize the academic catalog search results into structured data.

**Search Results Markdown:**
{search_context.markdown_content}

**Repository Name:** {search_context.repository_name}

**Expected Output Schema:**
{json.dumps(schema, indent=2)}

Please extract and organize the search results according to the schema.
Organize by:
- repository_relations: Publications/entities about the repository itself

Use only primitive types (strings, numbers, lists, dicts).
"""

    return prompt


async def search_academic_catalogs(
    repository_name: str,
) -> Dict[str, Any]:
    """
    Search academic catalogs using Infoscience tools.

    Stage 1 of the linked entities enrichment pipeline.

    Args:
        repository_name: Repository or tool name to search for

    Returns:
        Dictionary with 'data' (LinkedEntitiesContext) and 'usage' (dict with token info)
    """
    # Create context for the agent
    agent_context = {
        "repository_name": repository_name,
    }

    # Prepare the prompt (no author names)
    prompt = get_linked_entities_search_prompt(repository_name, [])

    # Add Infoscience search tools (only publications)
    tools = [
        search_infoscience_publications_tool,
    ]

    try:
        # Run agent with fallback across multiple models
        # Use str as output type - agent returns markdown text
        result = await run_agent_with_fallback(
            LINKED_ENTITIES_SEARCHER_CONFIGS,
            prompt,
            agent_context,
            str,  # Simple string output - just markdown text
            LINKED_ENTITIES_SEARCH_SYSTEM_PROMPT,
            tools,
        )

        # Extract the markdown string from PydanticAI result
        if hasattr(result, "output"):
            markdown_content = result.output
        else:
            markdown_content = result

        # Convert string to LinkedEntitiesContext
        if isinstance(markdown_content, str):
            search_context = LinkedEntitiesContext(
                markdown_content=markdown_content,
                repository_name=repository_name,
                author_names=[],  # No author search in atomic pipeline
            )
        else:
            # Fallback if we get something unexpected
            search_context = LinkedEntitiesContext(
                markdown_content=str(markdown_content),
                repository_name=repository_name,
                author_names=[],  # No author search in atomic pipeline
            )

        # Estimate tokens from prompt and response
        response_text = (
            markdown_content
            if isinstance(markdown_content, str)
            else str(markdown_content)
        )

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
            system_prompt=LINKED_ENTITIES_SEARCH_SYSTEM_PROMPT,
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

        # Log search results size
        if hasattr(search_context, "markdown_content"):
            search_markdown_size = len(search_context.markdown_content)
        else:
            search_markdown_size = 0

        logger.info(
            f"Academic catalog search completed: {search_markdown_size:,} chars of results",
        )

        return {
            "data": search_context,
            "usage": usage_data,
        }

    except Exception as e:
        logger.error(f"Academic catalog search failed: {e}", exc_info=True)
        return {
            "data": None,
            "usage": None,
        }


async def structure_linked_entities(
    search_context: LinkedEntitiesContext,
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Structure linked entities search results.

    Stage 2 of the linked entities enrichment pipeline.

    Args:
        search_context: Compiled search results from stage 1
        schema: Simplified schema definition

    Returns:
        Dictionary with 'data' (SimplifiedLinkedEntitiesResult) and 'usage' (dict with token info)
    """
    # Create context for the agent
    agent_context = {
        "search_context": search_context,
        "schema": schema,
    }

    # Prepare the prompt
    prompt = get_linked_entities_structure_prompt(search_context, schema)

    # No tools for structured output agent
    tools = []

    try:
        # Run agent with fallback across multiple models
        # Use dynamically generated simplified model
        result = await run_agent_with_fallback(
            LINKED_ENTITIES_SEARCHER_CONFIGS,
            prompt,
            agent_context,
            _SIMPLIFIED_LINKED_ENTITIES_MODEL,
            LINKED_ENTITIES_STRUCTURE_SYSTEM_PROMPT,
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
            response_text = json.dumps(structured_output)
        elif isinstance(structured_output, str):
            response_text = structured_output

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
            system_prompt=LINKED_ENTITIES_STRUCTURE_SYSTEM_PROMPT,
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

        logger.info("Linked entities structuring completed successfully")

        return {
            "data": structured_output,
            "usage": usage_data,
        }

    except Exception as e:
        logger.error(f"Linked entities structuring failed: {e}", exc_info=True)
        return {
            "data": None,
            "usage": None,
        }
