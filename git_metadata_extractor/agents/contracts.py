from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from git_metadata_extractor.agents.models import AgentResult, ProviderSet


class RuntimeAgent(Protocol):
    """Protocol for runtime-selectable v2 agent implementations."""

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        """Execute the agent using stage context and provider bundle."""

        ...
