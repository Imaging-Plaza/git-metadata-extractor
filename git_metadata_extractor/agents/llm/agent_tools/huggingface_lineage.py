"""Pydantic-AI tool for HuggingFace base_model lineage walks.

Backed by :meth:`HuggingFaceRagProvider.lineage`. Pure local DuckDB lookup
— no RCP / Qdrant cost. Use when an agent needs to know what a model was
fine-tuned from, or which models depend on a given base.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.huggingface_rag import HuggingFaceRagProvider

logger = logging.getLogger(__name__)


_DESCRIPTION = (
    "Walk the HuggingFace base_models DAG from a `repo_id` (e.g. "
    "'epfl-llm/meditron-7b'). Returns ancestors (parent models the repo "
    "was fine-tuned from, recursively) and descendants (other repos in "
    "our index that list `repo_id` as their base). Useful for: "
    "(a) 'what is this model fine-tuned from?' (ancestors), "
    "(b) 'what derivative models exist for this base?' (descendants), "
    "(c) tracing a lineage chain like Llama-2 → Meditron → some-finetune. "
    "`depth` controls how many hops to walk in each direction (default 3). "
    "Pure local DuckDB lookup — fast (sub-second) and doesn't burn RCP. "
    "Returns `{root, ancestors: {level_1, level_2, …}, descendants: "
    "{level_1, …}, edges: [{from, to}], depth}`."
)


def make_huggingface_lineage_tool(provider: HuggingFaceRagProvider) -> Tool:
    """Tool factory: walk the HF base_model graph."""

    async def lineage_huggingface(
        repo_id: str,
        depth: int = 3,
    ) -> dict[str, Any]:
        """HF lineage walk. See tool description."""
        logger.info(
            "tool call: lineage_huggingface — repo_id=%r depth=%d",
            repo_id, depth,
        )
        record_query(service="huggingface.rag.lineage", query=repo_id)
        return await provider.lineage(repo_id, depth=depth)

    return Tool(
        lineage_huggingface,
        name="lineage_huggingface",
        description=_DESCRIPTION,
    )


__all__ = ["make_huggingface_lineage_tool"]
