from __future__ import annotations

from enum import Enum


class AgentRuntime(str, Enum):
    """Supported runtime execution modes for v2 agents."""

    RULE_BASED = "rule_based"
    LLM = "llm"
    HYBRID = "hybrid"


def parse_agent_runtime(
    value: AgentRuntime | str | None,
    *,
    default: AgentRuntime = AgentRuntime.RULE_BASED,
    field_name: str = "agent_runtime",
) -> AgentRuntime:
    """Normalize a runtime selector into an ``AgentRuntime`` value.

    Accepts enum values, strings, and ``None`` (which resolves to ``default``).
    Raises ``ValueError`` with a deterministic message for invalid values.
    """

    if value is None:
        return default
    if isinstance(value, AgentRuntime):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized:
            try:
                return AgentRuntime(normalized)
            except ValueError:
                pass

    allowed_values = ", ".join(runtime.value for runtime in AgentRuntime)
    message = (
        f"Invalid runtime value for {field_name}: {value!r}. "
        f"Expected one of: {allowed_values}"
    )
    raise ValueError(message)
