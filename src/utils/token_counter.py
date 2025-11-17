"""
Token counting utilities using tiktoken.

Provides client-side token estimation as a complement to API-reported usage.
Useful for validating token counts and handling cases where APIs don't report usage.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Global tokenizer instance (lazy loaded)
_tokenizer = None


def get_tokenizer():
    """
    Get or create the tiktoken tokenizer instance.

    Uses cl100k_base encoding which is used by:
    - GPT-4, GPT-4 Turbo, GPT-4o
    - GPT-3.5-turbo

    This provides reasonable estimates for most modern LLMs.
    """
    global _tokenizer

    if _tokenizer is None:
        try:
            import tiktoken

            _tokenizer = tiktoken.get_encoding("cl100k_base")
            logger.debug("Initialized tiktoken with cl100k_base encoding")
        except ImportError:
            logger.warning(
                "tiktoken not installed. Token estimation will not be available. "
                "Install with: pip install tiktoken",
            )
            _tokenizer = False  # Mark as unavailable
        except Exception as e:
            logger.error(f"Failed to initialize tiktoken: {e}")
            _tokenizer = False

    return _tokenizer if _tokenizer is not False else None


def count_tokens(text: str) -> Optional[int]:
    """
    Count the number of tokens in a text string.

    Args:
        text: The text to count tokens for

    Returns:
        Number of tokens, or None if tokenizer is unavailable
    """
    if not text:
        return 0

    tokenizer = get_tokenizer()
    if tokenizer is None:
        return None

    try:
        tokens = tokenizer.encode(text)
        return len(tokens)
    except Exception as e:
        logger.error(f"Error counting tokens: {e}")
        return None


def estimate_tokens_from_messages(
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
    response: Optional[str] = None,
) -> dict:
    """
    Estimate token counts for a message exchange.

    Args:
        system_prompt: The system prompt sent to the model
        user_prompt: The user prompt/query sent to the model
        response: The model's response

    Returns:
        Dictionary with 'input_tokens', 'output_tokens', and 'total_tokens' estimates
    """
    input_tokens = 0
    output_tokens = 0

    # Count input tokens (system + user prompts)
    if system_prompt:
        system_count = count_tokens(system_prompt)
        if system_count is not None:
            input_tokens += system_count

    if user_prompt:
        user_count = count_tokens(user_prompt)
        if user_count is not None:
            input_tokens += user_count

    # Add overhead for message formatting (approximate)
    # OpenAI uses special tokens for role markers, etc.
    # This is a rough estimate: ~4 tokens per message
    if system_prompt or user_prompt:
        input_tokens += 4

    # Count output tokens
    if response:
        response_count = count_tokens(response)
        if response_count is not None:
            output_tokens = response_count

    return {
        "input_tokens": input_tokens if input_tokens > 0 else None,
        "output_tokens": output_tokens if output_tokens > 0 else None,
        "total_tokens": (input_tokens + output_tokens)
        if (input_tokens > 0 or output_tokens > 0)
        else None,
    }


def estimate_tokens_with_tools(
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
    response: Optional[str] = None,
    tool_calls: int = 0,
    tool_results_text: Optional[str] = None,
) -> dict:
    """
    Estimate token counts including tool call overhead.

    This function extends estimate_tokens_from_messages to account for:
    - Tool call overhead (function definitions and call structures)
    - Tool results text (returned data from tool executions)
    - Multiple LLM round-trips when tools are used

    Args:
        system_prompt: The system prompt sent to the model
        user_prompt: The user prompt/query sent to the model
        response: The model's response (final output after tool calls)
        tool_calls: Number of tool calls made during the agent run
        tool_results_text: Combined text of all tool results (optional, for more accurate estimation)

    Returns:
        Dictionary with 'input_tokens', 'output_tokens', and 'total_tokens' estimates
    """
    # Start with base estimation
    base_estimate = estimate_tokens_from_messages(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response=response,
    )

    input_tokens = base_estimate.get("input_tokens", 0) or 0
    output_tokens = base_estimate.get("output_tokens", 0) or 0

    # Add tool call overhead
    if tool_calls > 0:
        # Each tool call adds overhead:
        # - Function call structure: ~50 tokens
        # - Function definition in system prompt: ~50 tokens (one-time, but we estimate per call)
        # - Tool call formatting: ~20 tokens
        tool_call_overhead = tool_calls * 100  # Conservative estimate per tool call

        # Tool calls are part of the model's output (the model generates the function call)
        output_tokens += tool_call_overhead

        # Tool results become part of the input for the next request
        if tool_results_text:
            tool_results_tokens = count_tokens(tool_results_text)
            if tool_results_tokens is not None:
                # Add tool results as input tokens (they're sent back to the model)
                input_tokens += tool_results_tokens
                # Add formatting overhead for tool results (~20 tokens per result)
                input_tokens += tool_calls * 20
        else:
            # If we don't have tool results text, estimate based on tool calls
            # Assume each tool result is ~200 tokens on average
            estimated_tool_results = tool_calls * 200
            input_tokens += estimated_tool_results
            input_tokens += tool_calls * 20  # Formatting overhead

        # Additional overhead for tool-related message formatting
        # Each tool interaction adds ~10 tokens for message structure
        input_tokens += tool_calls * 10

    return {
        "input_tokens": input_tokens if input_tokens > 0 else 0,
        "output_tokens": output_tokens if output_tokens > 0 else 0,
        "total_tokens": input_tokens + output_tokens,
    }
