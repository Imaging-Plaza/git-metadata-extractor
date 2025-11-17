"""
Model Configuration System

Centralized configuration for different providers and models used across the application.
Supports OpenAI, OpenRouter, OpenAI-compatible endpoints, and Ollama (local and remote).
"""

import json
import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Default model configurations
MODEL_CONFIGS = {
    "run_llm_analysis": [
        {
            "provider": "openai-compatible",
            "model": "openai/gpt-oss-120b",
            "base_url": "https://inference.rcp.epfl.ch/v1",
            "api_key_env": "RCP_TOKEN",
            "max_retries": 3,
            "temperature": 0.2,
            "max_tokens": 16000,
            "timeout": 600.0,
            "allow_tools": True,  # Enable tool usage for this model
        },
        {
            "provider": "openai",
            "model": "o4-mini",
            "max_retries": 3,
            "temperature": 0.2,
            "max_tokens": 16000,
            "timeout": 600.0,
        },
        {
            "provider": "openrouter",
            "model": "google/gemini-2.5-flash",
            "max_retries": 3,
            "temperature": 0.2,
            "max_tokens": 16000,
            "timeout": 300.0,
        },
        {
            "provider": "ollama",
            "model": "llama3.2",
            "base_url": "http://localhost:11434/v1",
            "max_retries": 2,
            "temperature": 0.3,
            "timeout": 600.0,
        },
    ],
    "run_user_enrichment": [
        # {
        #     "provider": "openai-compatible",
        #     "model": "openai/gpt-oss-120b",
        #     "base_url": "https://inference.rcp.epfl.ch/v1",
        #     "api_key_env": "RCP_TOKEN",
        #     "max_retries": 2,
        #     "temperature": 0.1,
        #     "max_tokens": 8000,
        #     "timeout": 300.0,
        # },
        {
            "provider": "openai",
            "model": "o4-mini",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 8000,
            "timeout": 300.0,
        },
        {
            "provider": "openrouter",
            "model": "google/gemini-2.5-flash",
            "max_retries": 3,
            "temperature": 0.2,
            "max_tokens": 16000,
            "timeout": 300.0,
        },
    ],
    "run_organization_enrichment": [
        # {
        #     "provider": "openai-compatible",
        #     "model": "openai/gpt-oss-120b",
        #     "base_url": "https://inference.rcp.epfl.ch/v1",
        #     "api_key_env": "RCP_TOKEN",
        #     "max_retries": 2,
        #     "temperature": 0.1,
        #     "max_tokens": 8000,
        #     "timeout": 300.0,
        # },
        {
            "provider": "openai",
            "model": "o4-mini",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 8000,
            "timeout": 300.0,
        },
        {
            "provider": "openrouter",
            "model": "google/gemini-2.5-flash",
            "max_retries": 3,
            "temperature": 0.2,
            "max_tokens": 16000,
            "timeout": 300.0,
        },
    ],
    "run_linked_entities_enrichment": [
        # {
        #     "provider": "openai-compatible",
        #     "model": "openai/gpt-oss-120b",
        #     "base_url": "https://inference.rcp.epfl.ch/v1",
        #     "api_key_env": "RCP_TOKEN",
        #     "max_retries": 3,
        #     "temperature": 0.1,
        #     "max_tokens": 12000,
        #     "timeout": 300.0,
        # },
        {
            "provider": "openai",
            "model": "o4-mini",
            "max_retries": 3,
            "temperature": 0.1,
            "max_tokens": 12000,
            "timeout": 300.0,
        },
        {
            "provider": "openrouter",
            "model": "google/gemini-2.5-flash",
            "max_retries": 3,
            "temperature": 0.2,
            "max_tokens": 16000,
            "timeout": 300.0,
        },
    ],
    "run_epfl_assessment": [
        # {
        #     "provider": "openai-compatible",
        #     "model": "openai/gpt-oss-120b",
        #     "base_url": "https://inference.rcp.epfl.ch/v1",
        #     "api_key_env": "RCP_TOKEN",
        #     "max_retries": 2,
        #     "temperature": 0.1,
        #     "max_tokens": 8000,
        #     "timeout": 300.0,
        # },
        {
            "provider": "openai",
            "model": "o4-mini",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 8000,
            "timeout": 300.0,
        },
        {
            "provider": "openrouter",
            "model": "google/gemini-2.5-flash",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 8000,
            "timeout": 300.0,
        },
    ],
    "run_url_validation": [
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 4000,
            "timeout": 60.0,
        },
        {
            "provider": "openrouter",
            "model": "google/gemini-2.0-flash",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 4000,
            "timeout": 60.0,
        },
    ],
    "run_context_compiler": [
        {
            "provider": "openai-compatible",
            "model": "openai/gpt-oss-120b",
            "base_url": "https://inference.rcp.epfl.ch/v1",
            "api_key_env": "RCP_TOKEN",
            "max_retries": 3,
            "temperature": 0.2,
            "max_tokens": 16000,
            "timeout": 600.0,
            "allow_tools": False,  # No tools - only use repository content and GIMIE data
        },
    ],
    "run_structured_output": [
        {
            "provider": "openai-compatible",
            "model": "openai/gpt-oss-120b",
            "base_url": "https://inference.rcp.epfl.ch/v1",
            "api_key_env": "RCP_TOKEN",
            "max_retries": 3,
            "temperature": 0.2,
            "max_tokens": 16000,
            "timeout": 600.0,
            "allow_tools": False,  # No tools for structured output
        },
    ],
    "run_repository_classifier": [
        {
            "provider": "openai-compatible",
            "model": "openai/gpt-oss-120b",
            "base_url": "https://inference.rcp.epfl.ch/v1",
            "api_key_env": "RCP_TOKEN",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 8000,
            "timeout": 300.0,
            "allow_tools": False,  # No tools - classifies from compiled context
        },
    ],
    "run_organization_identifier": [
        {
            "provider": "openai-compatible",
            "model": "openai/gpt-oss-120b",
            "base_url": "https://inference.rcp.epfl.ch/v1",
            "api_key_env": "RCP_TOKEN",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 8000,
            "timeout": 300.0,
            "allow_tools": False,  # No tools - identifies from compiled context
        },
    ],
    "run_epfl_final_checker": [
        {
            "provider": "openai-compatible",
            "model": "openai/gpt-oss-120b",
            "base_url": "https://inference.rcp.epfl.ch/v1",
            "api_key_env": "RCP_TOKEN",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 16000,
            "timeout": 300.0,
            "allow_tools": False,  # No tools - analyzes enriched data only
        },
    ],
    "run_linked_entities_searcher": [
        {
            "provider": "openai-compatible",
            "model": "openai/gpt-oss-120b",
            "base_url": "https://inference.rcp.epfl.ch/v1",
            "api_key_env": "RCP_TOKEN",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 12000,
            "timeout": 400.0,
            "allow_tools": True,  # Needs Infoscience search tools
        },
    ],
}

# Environment variable mappings
ENV_VAR_MAPPINGS = {
    "run_llm_analysis": "LLM_ANALYSIS_MODELS",
    "run_user_enrichment": "USER_ENRICHMENT_MODELS",
    "run_organization_enrichment": "ORG_ENRICHMENT_MODELS",
    "run_linked_entities_enrichment": "linked_entities_ENRICHMENT_MODELS",
    "run_epfl_assessment": "EPFL_ASSESSMENT_MODELS",
    "run_repository_classifier": "REPOSITORY_CLASSIFIER_MODELS",
    "run_organization_identifier": "ORGANIZATION_IDENTIFIER_MODELS",
    "run_url_validation": "URL_VALIDATION_MODELS",
    "run_context_compiler": "CONTEXT_COMPILER_MODELS",
    "run_structured_output": "STRUCTURED_OUTPUT_MODELS",
    "run_epfl_final_checker": "EPFL_FINAL_CHECKER_MODELS",
    "run_linked_entities_searcher": "LINKED_ENTITIES_SEARCHER_MODELS",
}


def load_model_config(analysis_type: str) -> List[Dict[str, Any]]:
    """
    Load model configuration for a specific analysis type.

    Args:
        analysis_type: The analysis type (e.g., "run_llm_analysis")

    Returns:
        List of model configurations
    """
    # Check for environment variable override
    env_var = ENV_VAR_MAPPINGS.get(analysis_type)
    if env_var and os.getenv(env_var):
        try:
            env_config = json.loads(os.getenv(env_var))
            logger.info(f"Using environment variable configuration for {analysis_type}")
            return env_config
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {env_var}: {e}")
            logger.info(f"Falling back to default configuration for {analysis_type}")

    # Return default configuration
    return MODEL_CONFIGS.get(analysis_type, [])


def create_pydantic_ai_model(config: Dict[str, Any]):
    """
    Create a PydanticAI model from configuration using proper providers.

    Args:
        config: Model configuration dictionary

    Returns:
        PydanticAI model instance
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.ollama import OllamaProvider
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    provider = config.get("provider", "openai")
    model_name = config.get("model", "gpt-4o")

    if provider == "openai":
        return OpenAIChatModel(model_name)
    elif provider == "openrouter":
        return OpenAIChatModel(
            model_name,
            provider=OpenRouterProvider(api_key=os.getenv("OPENROUTER_API_KEY")),
        )
    elif provider == "openai-compatible":
        # For OpenAI-compatible endpoints, use OpenAIProvider with base_url
        api_key_env = config.get("api_key_env", "OPENAI_API_KEY")
        base_url = config.get("base_url")
        if not base_url:
            raise ValueError("openai-compatible provider requires base_url")
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                base_url=base_url,
                api_key=os.getenv(api_key_env),
            ),
        )
    elif provider == "ollama":
        base_url = config.get("base_url", "http://localhost:11434/v1")
        # Ensure base_url ends with /v1 for Ollama
        if not base_url.endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        return OpenAIChatModel(
            model_name,
            provider=OllamaProvider(base_url=base_url),
        )
    else:
        logger.warning(f"Unknown provider {provider}, defaulting to openai")
        return OpenAIChatModel(model_name)


# Old helper functions removed - now using proper PydanticAI providers


def get_model_parameters(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get model parameters from configuration, filtering out non-parameter keys.

    Args:
        config: Model configuration dictionary

    Returns:
        Dictionary of model parameters
    """
    # Keys that are not model parameters
    non_param_keys = {
        "provider",
        "model",
        "max_retries",
        "timeout",
        "base_url",
        "api_key_env",
        "max_completion_tokens",
        "allow_tools",  # Tool access flag, not a model parameter
    }

    # Filter out non-parameter keys
    params = {k: v for k, v in config.items() if k not in non_param_keys}

    # Handle special cases for different providers
    provider = config.get("provider", "openai")

    if provider == "ollama":
        # Ollama uses different parameter names
        if "max_tokens" in params:
            params["num_predict"] = params.pop("max_tokens")

    # Handle OpenAI reasoning models
    if provider == "openai" and config.get("model", "").startswith(("o3", "o4")):
        # Reasoning models use max_completion_tokens instead of max_tokens
        if "max_completion_tokens" in config:
            params["max_completion_tokens"] = config["max_completion_tokens"]
            params.pop("max_tokens", None)
        # Reasoning models don't use temperature
        params.pop("temperature", None)

    return params


def validate_config(config: Dict[str, Any]) -> bool:
    """
    Validate a model configuration.

    Args:
        config: Model configuration dictionary

    Returns:
        True if valid, False otherwise
    """
    required_keys = ["provider", "model", "max_retries"]

    for key in required_keys:
        if key not in config:
            logger.error(f"Missing required key '{key}' in model configuration")
            return False

    provider = config.get("provider")

    # Validate provider-specific requirements
    if provider == "openai-compatible":
        if "base_url" not in config:
            logger.error("openai-compatible provider requires 'base_url'")
            return False

    if provider == "ollama":
        # base_url is optional for Ollama (defaults to localhost)
        pass

    # Validate retry count
    max_retries = config.get("max_retries", 0)
    if not isinstance(max_retries, int) or max_retries < 1:
        logger.error("max_retries must be a positive integer")
        return False

    return True


def get_retry_delay(attempt: int) -> float:
    """
    Calculate retry delay using exponential backoff.

    Args:
        attempt: Current attempt number (0-based)

    Returns:
        Delay in seconds
    """
    return 2**attempt  # 2s, 4s, 8s, etc.
