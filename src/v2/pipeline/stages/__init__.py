"""Pipeline stages for the v2 extraction orchestrator."""

from src.v2.pipeline.stages.article_validation import validate_articles
from src.v2.pipeline.stages.author_validation import validate_author_classes
from src.v2.pipeline.stages.concept_tagging import (
    BACKEND_EPFL_GRAPH,
    BACKEND_LLM,
    BACKEND_WIKIPEDIA,
    SUPPORTED_BACKENDS,
    ConceptTaggingResult,
    run_concept_tagging_stage,
)
from src.v2.pipeline.stages.concept_tagging import (
    is_enabled as concept_tagging_is_enabled,
)
from src.v2.pipeline.stages.concept_tagging import (
    resolve_backend as concept_tagging_resolve_backend,
)
from src.v2.pipeline.stages.context_gather import gather_context
from src.v2.pipeline.stages.jsonld_build import build_jsonld_output
from src.v2.pipeline.stages.link_veracity import (
    LinkVeracityStageResult,
    apply_link_pruning_to_assembled_output,
    collect_unique_http_link_contexts,
    promote_failed_id_entities,
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
from src.v2.pipeline.stages.org_relationships import run_org_relationships_stage
from src.v2.pipeline.stages.output_assembly import (
    RootEntityValidationError,
    assemble_output,
    build_json_output,
)
from src.v2.pipeline.stages.ownership_check import (
    demote_github_props_to_units,
    emit_fork_parent_stubs,
    guarantee_repo_author,
    infer_article_source_organization,
    infer_github_handle_parents,
    infer_org_units,
    infer_owners,
    validate_ownership,
)
from src.v2.pipeline.stages.prune_dangling_refs import prune_dangling_refs
from src.v2.pipeline.stages.rule_based_disciplines import (
    tag_disciplines as tag_rule_based_disciplines,
)
from src.v2.pipeline.stages.reconciliation import reconcile_entities
from src.v2.pipeline.stages.refine_with_llm import (
    RefineWithLLMResult,
    run_refine_with_llm_stage,
)
from src.v2.pipeline.stages.refine_with_llm import (
    is_enabled as hybrid_refiner_is_enabled,
)
from src.v2.pipeline.stages.stats import compute_stats

__all__ = [
    "BACKEND_EPFL_GRAPH",
    "BACKEND_LLM",
    "BACKEND_WIKIPEDIA",
    "SUPPORTED_BACKENDS",
    "AssembledOutput",
    "ConceptTaggingResult",
    "ContextBundle",
    "LLMCriticStageResult",
    "LLMDedupStageResult",
    "LinkVeracityStageResult",
    "ReconciledEntities",
    "RefineWithLLMResult",
    "RootEntityValidationError",
    "apply_link_pruning_to_assembled_output",
    "assemble_output",
    "build_json_output",
    "build_jsonld_output",
    "collect_unique_http_link_contexts",
    "compute_stats",
    "concept_tagging_is_enabled",
    "concept_tagging_resolve_backend",
    "demote_github_props_to_units",
    "emit_fork_parent_stubs",
    "gather_context",
    "guarantee_repo_author",
    "infer_article_source_organization",
    "hybrid_refiner_is_enabled",
    "infer_github_handle_parents",
    "infer_org_units",
    "infer_owners",
    "promote_failed_id_entities",
    "prune_dangling_refs",
    "reconcile_entities",
    "run_concept_tagging_stage",
    "run_link_veracity_stage",
    "run_llm_critic_stage",
    "run_llm_dedup_stage",
    "run_org_relationships_stage",
    "run_refine_with_llm_stage",
    "tag_rule_based_disciplines",
    "validate_articles",
    "validate_author_classes",
    "validate_ownership",
]
