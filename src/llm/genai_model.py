import asyncio
import logging
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pydantic_ai import Agent

from ..agents.prompts import (
    system_prompt_json,
)
from ..data_models import SoftwareSourceCode
from ..utils.url_validation import (
    validate_and_clean_urls,
    validate_author_urls,
    validate_organization_urls,
    validate_software_image_urls,
)
from ..utils.utils import (
    is_github_repo_public,
    json_to_jsonLD,
)
from ..validation import Verification
from .model_config import (
    create_pydantic_ai_model,
    get_retry_delay,
    load_model_config,
    validate_config,
)
from .repo_context import prepare_repository_context, sanitize_special_tokens

# Setup logger first, before anything else
logger = logging.getLogger(__name__)

load_dotenv()

# Load model configurations
llm_analysis_configs = load_model_config("run_llm_analysis")
user_enrichment_configs = load_model_config("run_user_enrichment")
org_enrichment_configs = load_model_config("run_organization_enrichment")

# Validate configurations
for config in llm_analysis_configs:
    if not validate_config(config):
        logger.error(f"Invalid configuration for LLM analysis: {config}")
        raise ValueError("Invalid model configuration")

for config in user_enrichment_configs:
    if not validate_config(config):
        logger.error(f"Invalid configuration for user enrichment: {config}")
        raise ValueError("Invalid model configuration")

for config in org_enrichment_configs:
    if not validate_config(config):
        logger.error(f"Invalid configuration for organization enrichment: {config}")
        raise ValueError("Invalid model configuration")

# Agent cleanup tracking
_active_agents = []


class RepositoryAnalysisContext:
    """Context for repository analysis agent."""

    def __init__(
        self,
        repo_url: str,
        git_authors: List[Any],
        gimie_output: Optional[Any] = None,
    ):
        self.repo_url = repo_url
        self.git_authors = git_authors
        self.gimie_output = gimie_output


def create_agent_from_config(config: Dict[str, Any]) -> Agent:
    """
    Create a PydanticAI agent from configuration.

    Args:
        config: Model configuration dictionary

    Returns:
        Configured PydanticAI agent
    """
    model = create_pydantic_ai_model(config)

    # Create agent with the model
    agent = Agent(
        model=model,
        output_type=SoftwareSourceCode,
        system_prompt=system_prompt_json,
    )

    # Track agent for cleanup
    _active_agents.append(agent)

    return agent


async def cleanup_agents():
    """
    Cleanup all active agents to free memory.
    This should be called periodically or on application shutdown.
    """
    global _active_agents

    if not _active_agents:
        logger.debug("No active agents to cleanup")
        return

    logger.info(f"Cleaning up {len(_active_agents)} active agents")

    for agent in _active_agents.copy():
        try:
            # PydanticAI agents don't have explicit cleanup methods,
            # but we can remove them from tracking and let GC handle them
            _active_agents.remove(agent)
            logger.debug("Agent removed from tracking")
        except Exception as e:
            logger.warning(f"Error during agent cleanup: {e}")

    # Force garbage collection
    import gc

    gc.collect()

    logger.info("Agent cleanup completed")


def get_active_agents_count() -> int:
    """Get the number of currently active agents."""
    return len(_active_agents)


async def run_agent_with_retry(
    agent: Agent,
    prompt: str,
    context: Any,
    config: Dict[str, Any],
) -> Any:
    """
    Run agent with retry logic and exponential backoff.

    Args:
        agent: PydanticAI agent
        prompt: Input prompt
        context: Agent context
        config: Model configuration

    Returns:
        Agent result

    Raises:
        Exception: If all retries fail
    """
    max_retries = config.get("max_retries", 3)
    last_exception = None

    for attempt in range(max_retries):
        try:
            logger.info(f"Attempting agent run (attempt {attempt + 1}/{max_retries})")
            result = await agent.run(prompt, deps=context)
            logger.info(f"Agent run successful on attempt {attempt + 1}")
            return result
        except Exception as e:
            last_exception = e
            logger.warning(f"Agent run failed on attempt {attempt + 1}: {e}")

            if attempt < max_retries - 1:
                delay = get_retry_delay(attempt)
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"All {max_retries} attempts failed")

    raise last_exception or Exception("Agent run failed")


async def run_agent_with_fallback(
    agent_configs: List[Dict[str, Any]],
    prompt: str,
    context: Any,
) -> Any:
    """
    Run agent with fallback to next model if current fails.

    Args:
        agent_configs: List of agent configurations to try
        prompt: Input prompt
        context: Agent context

    Returns:
        Agent result

    Raises:
        Exception: If all models fail
    """
    last_exception = None

    for i, config in enumerate(agent_configs):
        try:
            logger.info(
                f"Trying model {i + 1}/{len(agent_configs)}: {config['provider']}/{config['model']}",
            )
            agent = create_agent_from_config(config)
            result = await run_agent_with_retry(agent, prompt, context, config)
            logger.info(f"Successfully completed with model {i + 1}")
            return result
        except Exception as e:
            last_exception = e
            logger.error(f"Model {i + 1} failed: {e}")
            if i < len(agent_configs) - 1:
                logger.info("Falling back to next model...")
            else:
                logger.error("All models failed")

    raise last_exception or Exception("All models failed")


# These functions have been moved to repo_context.py


async def llm_request_repo_infos(
    repo_url: str,
    output_format: str = "json-ld",
    gimie_output: Optional[Any] = None,
    max_tokens: int = 40000,
) -> Optional[Dict[str, Any]]:
    """
    Analyze repository using PydanticAI with multi-provider support and retry/fallback logic.

    Args:
        repo_url: Repository URL to analyze
        output_format: Output format ("json" or "json-ld")
        gimie_output: Optional GIMIE output to include
        max_tokens: Maximum tokens for input text

    Returns:
        Analysis result or None if failed
    """
    # Check if the repository is public before proceeding
    if not is_github_repo_public(repo_url):
        logger.error(
            f"Cannot process repository: {repo_url} is not public or not accessible",
        )
        return None

    # Prepare repository context
    context_result = await prepare_repository_context(repo_url, max_tokens)

    if not context_result["success"]:
        if context_result["error"] == "Repository has no analyzable content":
            # Return minimal valid metadata for empty repositories
            repo_name = repo_url.rstrip("/").split("/")[-1]
            return {
                "@context": "https://schema.org/",
                "@type": "SoftwareSourceCode",
                "name": repo_name,
                "codeRepository": repo_url,
                "description": "Repository appears to be empty or has no analyzable content",
            }
        else:
            logger.error(
                f"Failed to prepare repository context: {context_result['error']}",
            )
            return None

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
    prompt = f"""Analyze the following software repository and extract comprehensive metadata.

Repository URL: {repo_url}

Repository Content:
{input_text}

Please provide a detailed analysis including:
- Repository name, description, and purpose
- Programming languages used
- License information
- Author information and affiliations
- Related organizations
- Keywords and topics
- Any other relevant metadata

Focus on accuracy and completeness in your analysis."""

    try:
        # Run agent with fallback across multiple models
        result = await run_agent_with_fallback(
            llm_analysis_configs,
            prompt,
            agent_context,
        )

        # Extract the output from PydanticAI result
        if hasattr(result, "output"):
            json_data = result.output
        else:
            json_data = result

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

        # Validate organization URLs in relatedToOrganizationsROR
        if (
            "relatedToOrganizationsROR" in json_data
            and json_data["relatedToOrganizationsROR"]
        ):
            validated_orgs = []
            for org in json_data["relatedToOrganizationsROR"]:
                if isinstance(org, dict):
                    validated_orgs.append(validate_organization_urls(org))
                else:
                    validated_orgs.append(org)
            json_data["relatedToOrganizationsROR"] = validated_orgs

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
        verifier = Verification(json_data, repo_url)
        verifier.run()
        verifier.summary()

        cleaned_json = verifier.sanitize_metadata()

        context_path = "src/files/json-ld-context.json"
        if output_format == "json-ld":
            return json_to_jsonLD(cleaned_json, context_path)
        elif output_format == "json":
            return cleaned_json
        else:
            logger.error(f"Unsupported output format: {output_format}")
            return None

    except Exception as e:
        logger.error(f"Error in repository analysis: {e}")
        # Cleanup agents even on error
        await cleanup_agents()
        return None


# Old API functions removed - now using PydanticAI with multi-provider support
