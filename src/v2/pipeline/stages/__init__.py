"""Pipeline stages for the v2 extraction orchestrator."""

from src.v2.pipeline.stages.context_gather import gather_context
from src.v2.pipeline.stages.intermediates import assemble_intermediates
from src.v2.pipeline.stages.jsonld_build import build_jsonld_output
from src.v2.pipeline.stages.link_veracity import (
    LinkVeracityStageResult,
    apply_link_pruning_to_assembled_output,
    collect_unique_http_link_contexts,
    run_link_veracity_stage,
)
from src.v2.pipeline.stages.llm_critic import run_llm_critic_stage
from src.v2.pipeline.stages.llm_dedup import run_llm_dedup_stage
from src.v2.pipeline.stages.models import (
    AssembledOutput,
    ContextBundle,
    LLMCriticStageResult,
    LLMDedupStageResult,
    ReconciledEntities,
)
from src.v2.pipeline.stages.output_assembly import (
    RootEntityValidationError,
    assemble_output,
    build_json_output,
)
from src.v2.pipeline.stages.reconciliation import reconcile_entities
from src.v2.pipeline.stages.stats import compute_stats

__all__ = [
    "AssembledOutput",
    "ContextBundle",
    "ReconciledEntities",
    "RootEntityValidationError",
    "assemble_intermediates",
    "assemble_output",
    "build_json_output",
    "build_jsonld_output",
    "collect_unique_http_link_contexts",
    "compute_stats",
    "gather_context",
    "LLMCriticStageResult",
    "LLMDedupStageResult",
    "LinkVeracityStageResult",
    "apply_link_pruning_to_assembled_output",
    "reconcile_entities",
    "run_llm_critic_stage",
    "run_llm_dedup_stage",
    "run_link_veracity_stage",
]
