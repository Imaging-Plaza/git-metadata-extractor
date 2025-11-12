"""
Repository analysis agent
"""

import logging
from typing import Any, Optional

from ..context import prepare_repository_context
from ..context.infoscience import (
    get_author_publications_tool,
    search_infoscience_publications_tool,
)
from ..data_models.repository import RepositoryAnalysisContext, SoftwareSourceCode
from ..llm.model_config import (
    load_model_config,
    validate_config,
)
from ..utils.token_counter import estimate_tokens_from_messages
from ..utils.url_validation import (
    validate_and_clean_urls,
    validate_author_urls,
    validate_organization_urls,
    validate_software_image_urls,
)
from ..utils.utils import sanitize_special_tokens
from .agents_management import cleanup_agents, run_agent_with_fallback
from .repository_prompts import get_repo_general_prompt, system_prompt_repository

################################################################
#
###############################################################

# Setup logger first, before anything else
logger = logging.getLogger(__name__)


# Load model configurations
llm_analysis_configs = load_model_config("run_llm_analysis")

# Validate configurations
for config in llm_analysis_configs:
    if not validate_config(config):
        logger.error(f"Invalid configuration for LLM analysis: {config}")
        raise ValueError("Invalid model configuration")


##########################################################
# Basic LLM Analysis
##########################################################


async def llm_request_repo_infos(
    repo_url: str,
    gimie_output: Optional[Any] = None,
    max_tokens: int = 40000,
) -> dict:
    """
    Analyze repository using PydanticAI with multi-provider support and retry/fallback logic.

    Args:
        repo_url: Repository URL to analyze
        output_format: Output format ("json" or "json-ld")
        gimie_output: Optional GIMIE output to include
        max_tokens: Maximum tokens for input text

    Returns:
        Dictionary with 'data' (SoftwareSourceCode) and 'usage' (dict with token info) keys,
        or {'data': None, 'usage': None} if failed
    """

    # Prepare repository context
    context_result = await prepare_repository_context(repo_url, max_tokens)

    if not context_result["success"]:
        if context_result["error"] == "Repository has no analyzable content":
            # Return minimal valid metadata for empty repositories
            repo_name = repo_url.rstrip("/").split("/")[-1]
            return {
                "data": {
                    "@context": "https://schema.org/",
                    "@type": "SoftwareSourceCode",
                    "name": repo_name,
                    "codeRepository": repo_url,
                    "description": "Repository appears to be empty or has no analyzable content",
                },
                "usage": None,
            }
        else:
            logger.error(
                f"Failed to prepare repository context: {context_result['error']}",
            )
            return {"data": None, "usage": None}

    input_text = context_result["input_text"]
    git_authors = context_result["git_authors"]

    # Add GIMIE output if provided
    if gimie_output:
        gimie_text = str(gimie_output)
        gimie_text = sanitize_special_tokens(gimie_text)
        input_text += "\n\n" + gimie_text

    # Create context for the agent
    agent_context = RepositoryAnalysisContext(
        repo_url=repo_url,
        git_authors=git_authors,
        gimie_output=gimie_output,
    )

    # Prepare the prompt
    prompt = get_repo_general_prompt(repo_url, input_text)

    try:
        # Define tools for the repository agent
        tools = [
            search_infoscience_publications_tool,
            get_author_publications_tool,
        ]

        # Run agent with fallback across multiple models
        result = await run_agent_with_fallback(
            llm_analysis_configs,
            prompt,
            agent_context,
            SoftwareSourceCode,
            system_prompt_repository,
            tools,
        )

        # Extract the output from PydanticAI result
        if hasattr(result, "output"):
            json_data = result.output
        else:
            json_data = result

        # Estimate tokens from prompt and response (client-side count)
        response_text = ""
        if hasattr(json_data, "model_dump_json"):
            response_text = json_data.model_dump_json()
        elif isinstance(json_data, dict):
            import json as json_module

            response_text = json_module.dumps(json_data)
        elif isinstance(json_data, str):
            response_text = json_data

        estimated = estimate_tokens_from_messages(
            system_prompt=system_prompt_repository,
            user_prompt=prompt,
            response=response_text,
        )

        # Extract usage information from the result
        usage_data = None

        if hasattr(result, "usage"):
            usage = result.usage

            # First try to get tokens from direct attributes
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0

            # If tokens are 0, check the details field (for Anthropic, OpenAI reasoning models, etc.)
            # See: https://github.com/pydantic/pydantic-ai/issues/3223
            if input_tokens == 0 and output_tokens == 0 and hasattr(usage, "details"):
                details = usage.details
                if isinstance(details, dict):
                    input_tokens = details.get("input_tokens", 0) or 0
                    output_tokens = details.get("output_tokens", 0) or 0
                    logger.debug(
                        f"Extracted tokens from usage.details: input={input_tokens}, output={output_tokens}",
                    )

            usage_data = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_input_tokens": estimated.get("input_tokens", 0),
                "estimated_output_tokens": estimated.get("output_tokens", 0),
            }
            logger.info(
                f"Repository agent token usage - Input: {input_tokens}, Output: {output_tokens}",
            )
            logger.info(
                f"Repository agent estimated - Input: {estimated.get('input_tokens', 0)}, Output: {estimated.get('output_tokens', 0)}",
            )
        else:
            logger.warning("Result object has no 'usage' attribute")
            # Use estimates as fallback
            usage_data = {
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_input_tokens": estimated.get("input_tokens", 0),
                "estimated_output_tokens": estimated.get("output_tokens", 0),
            }

        # Ensure it's a dictionary
        if hasattr(json_data, "model_dump"):
            json_data = json_data.model_dump()

        logger.info("Successfully received analysis from agent")

        # Validate and clean URLs in the LLM output
        logger.info("Validating URLs in LLM output...")
        json_data = validate_and_clean_urls(json_data)

        # Validate author URLs
        if "author" in json_data and json_data["author"]:
            validated_authors = []
            for author in json_data["author"]:
                if isinstance(author, dict):
                    validated_authors.append(validate_author_urls(author))
                else:
                    validated_authors.append(author)
            json_data["author"] = validated_authors

        # Validate organization URLs in relatedToOrganizations
        if (
            "relatedToOrganizations" in json_data
            and json_data["relatedToOrganizations"]
        ):
            validated_orgs = []
            for org in json_data["relatedToOrganizations"]:
                if isinstance(org, dict):
                    validated_orgs.append(validate_organization_urls(org))
                elif isinstance(org, str):
                    validated_orgs.append(org)
                else:
                    validated_orgs.append(org)
            json_data["relatedToOrganizations"] = validated_orgs

        # Validate software image URLs
        if "hasSoftwareImage" in json_data and json_data["hasSoftwareImage"]:
            validated_images = []
            for image in json_data["hasSoftwareImage"]:
                if isinstance(image, dict):
                    validated_images.append(validate_software_image_urls(image))
                else:
                    validated_images.append(image)
            json_data["hasSoftwareImage"] = validated_images

        # Cleanup agents after successful completion
        await cleanup_agents()

        # Add git authors to the JSON data
        if git_authors:
            json_data["gitAuthors"] = [
                {
                    "name": author.name,
                    "email": author.email,
                    "commits": {
                        "total": author.commits.total,
                        "firstCommitDate": (
                            str(author.commits.firstCommitDate)
                            if author.commits.firstCommitDate
                            else None
                        ),
                        "lastCommitDate": (
                            str(author.commits.lastCommitDate)
                            if author.commits.lastCommitDate
                            else None
                        ),
                    }
                    if author.commits
                    else None,
                }
                for author in git_authors
            ]

        # Run verification before converting to JSON-LD
        # verifier = Verification(json_data, repo_url)
        # verifier.run()
        # verifier.summary()

        # cleaned_json = verifier.sanitize_metadata()

        # context_path = "src/files/json-ld-context.json"
        # if output_format == "json-ld":
        #     return json_to_jsonLD(cleaned_json, context_path)
        # elif output_format == "json":
        #     return cleaned_json
        # else:
        #     logger.error(f"Unsupported output format: {output_format}")
        #     return None

        return {
            "data": SoftwareSourceCode.model_validate(json_data),
            "usage": usage_data,
        }

    except Exception as e:
        logger.error(f"Error in repository analysis: {e}")
        # Cleanup agents even on error
        await cleanup_agents()
        return {"data": None, "usage": None}
