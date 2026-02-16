"""
Agents Management
"""

import asyncio
import logging
from typing import Any, Dict, List

from dotenv import load_dotenv
from openai import BaseModel
from pydantic_ai import Agent

from ..llm.model_config import (
    create_pydantic_ai_model,
    get_retry_delay,
    load_model_config,
    validate_config,
)

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


##########################################################
# Agents Management
##########################################################


def create_agent_from_config(
    config: Dict[str, Any],
    output_type: BaseModel,
    system_prompt: str,
    tools: List[Any] = None,
) -> Agent:
    """
    Create a PydanticAI agent from configuration.

    Args:
        config: Model configuration dictionary
        output_type: Pydantic model for output validation
        system_prompt: System prompt for the agent
        tools: Optional list of tool functions to register with the agent

    Returns:
        Configured PydanticAI agent
    """
    model = create_pydantic_ai_model(config)

    # Check if tools are allowed for this model configuration
    # Default to True if not specified (backward compatibility)
    allow_tools = config.get("allow_tools", True)

    # Only register tools if allowed and tools are provided
    agent_tools = []
    if allow_tools and tools:
        agent_tools = tools
    elif not allow_tools and tools:
        logger.warning(
            f"Tools provided but allow_tools=False for {config.get('provider')}/{config.get('model')}. "
            "Tools will not be registered.",
        )

    # Create agent with the model and optional tools
    agent = Agent(
        model=model,
        output_type=output_type,  # SoftwareSourceCode,
        system_prompt=system_prompt,  # system_prompt_json,
        tools=agent_tools,  # Register tools only if allowed
        retries=3,  # Allow model to retry up to 3 times on tool calls and output validation
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
            error_msg = str(e)

            # Log more details about validation errors
            if "validation" in error_msg.lower() or "retries" in error_msg.lower():
                logger.error(
                    f"Agent run failed on attempt {attempt + 1} with validation error: {e}",
                    exc_info=True,  # Include full traceback
                )

                # Try to extract Pydantic ValidationError details
                validation_error = None
                current_exc = e

                # Check the exception itself first
                if hasattr(current_exc, "errors") and callable(current_exc.errors):
                    try:
                        validation_error = current_exc
                        logger.error("Found ValidationError in main exception")
                    except Exception:
                        pass

                # Traverse exception chain to find ValidationError
                if not validation_error:
                    visited = set()
                    to_check = [e]
                    if hasattr(e, "__cause__") and e.__cause__:
                        to_check.append(e.__cause__)
                    if hasattr(e, "__context__") and e.__context__:
                        to_check.append(e.__context__)

                    depth = 0
                    while to_check and depth < 15:
                        current = to_check.pop(0)
                        if id(current) in visited:
                            continue
                        visited.add(id(current))

                        # Check if this is a ValidationError
                        if hasattr(current, "errors") and callable(current.errors):
                            try:
                                errors = current.errors()
                                if errors:
                                    validation_error = current
                                    logger.error(
                                        f"Found ValidationError at depth {depth}",
                                    )
                                    break
                            except Exception:
                                pass

                        # Check for pydantic_core.ValidationError
                        if type(
                            current,
                        ).__name__ == "ValidationError" or "ValidationError" in str(
                            type(current),
                        ):
                            try:
                                if hasattr(current, "errors"):
                                    errors = current.errors()
                                    if errors:
                                        validation_error = current
                                        logger.error(
                                            f"Found ValidationError (pydantic_core) at depth {depth}",
                                        )
                                        break
                            except Exception:
                                pass

                        # Add nested exceptions to check
                        if hasattr(current, "__cause__") and current.__cause__:
                            to_check.append(current.__cause__)
                        if hasattr(current, "__context__") and current.__context__:
                            to_check.append(current.__context__)

                        depth += 1

                # Log validation error details if found
                if validation_error and hasattr(validation_error, "errors"):
                    try:
                        errors = validation_error.errors()
                        logger.error("=" * 80)
                        logger.error(
                            f"PYDANTIC VALIDATION ERRORS ({len(errors)} errors):",
                        )
                        logger.error("=" * 80)
                        for i, error in enumerate(errors, 1):
                            field_path = " -> ".join(
                                str(loc) for loc in error.get("loc", [])
                            )
                            logger.error(f"Error {i}:")
                            logger.error(f"  Field path: {field_path}")
                            logger.error(f"  Error type: {error.get('type', 'N/A')}")
                            logger.error(f"  Message: {error.get('msg', 'N/A')}")
                            logger.error(f"  Input value: {error.get('input', 'N/A')}")
                            if "ctx" in error:
                                logger.error(f"  Context: {error['ctx']}")
                        logger.error("=" * 80)
                    except Exception as parse_err:
                        logger.error(f"Failed to parse validation errors: {parse_err}")

                # Try to extract raw LLM output from exception attributes
                if hasattr(e, "args") and e.args:
                    for arg in e.args:
                        if isinstance(arg, dict):
                            logger.error(f"Exception args dict: {arg}")
                        elif isinstance(arg, str) and len(arg) > 100:
                            logger.error(
                                f"Exception args (first 500 chars): {arg[:500]}",
                            )

                # Log exception attributes that might contain LLM output
                for attr in ["output", "raw_output", "response", "data", "result"]:
                    if hasattr(e, attr):
                        value = getattr(e, attr)
                        if value is not None:
                            logger.error(
                                f"Exception.{attr}: {type(value)} = {str(value)[:500]}",
                            )
            else:
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
    output_type: BaseModel,
    system_prompt: str,
    tools: List[Any] = None,
) -> Any:
    """
    Run agent with fallback to next model if current fails.

    Args:
        agent_configs: List of agent configurations to try
        prompt: Input prompt
        context: Agent context
        output_type: Pydantic model for output validation
        system_prompt: System prompt for the agent
        tools: Optional list of tool functions to register with the agent

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
            agent = create_agent_from_config(config, output_type, system_prompt, tools)
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
