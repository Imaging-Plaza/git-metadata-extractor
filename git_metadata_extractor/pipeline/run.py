"""The post-agent stage sequence, as a list instead of a 1,110-line function.

This is the ordered sequence that used to be inlined in
`api/extract.py::extract`. Each entry is a thin adapter over the existing stage
function: same call, same log line, same warning plumbing, same fail-open
behaviour. Nothing here changes what the pipeline computes — the corpus differ
(`scripts/v2/corpus_signature.py`) must report an empty diff against
`tests/v2/corpus/baseline.signature.json`.

Two details worth knowing before editing:

**Stage names are log names, not function names.** `validate_ownership` appears
twice, logging as `ownership_check` and then `inverse_consistency`;
`infer_owners` logs as `owner_inference`. The names here reproduce the log
output the route produced, so operators' greps keep working.

**Almost every stage is fail-closed.** Only `org_relationships` was wrapped in
`try/except` in the route; the rest let exceptions escape to FastAPI as a 500.
Making them uniformly forgiving would convert hard failures into silently
partial graphs, so `fail_open=True` is set per stage and only where it was
already true. Shrinking that set is plan phase 8's job.
"""

from __future__ import annotations

from copy import deepcopy
from time import perf_counter
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.agents.runtime import AgentRuntime
from git_metadata_extractor.pipeline.runner import Stage, StageGate
from git_metadata_extractor.pipeline.stages import (
    demote_github_props_to_units,
    emit_fork_parent_stubs,
    guarantee_repo_author,
    infer_article_source_organization,
    infer_github_handle_parents,
    infer_org_units,
    infer_owners,
    prune_dangling_refs,
    run_org_relationships_stage,
    run_resolve_bio_to_ror_llm_stage,
    run_resolve_bio_to_ror_stage,
    run_resolve_company_to_ror_stage,
    run_resolve_placeholder_orgs_to_ror_stage,
    validate_articles,
    validate_author_classes,
    validate_ownership,
)
from git_metadata_extractor.pipeline.stages.models import AssembledOutput
from git_metadata_extractor.pipeline.stages.validate_org_github_handles import (
    validate_org_github_handles,
)

if TYPE_CHECKING:
    from git_metadata_extractor.pipeline.state import PipelineState

import logging

logger = logging.getLogger("git_metadata_extractor.api.extract")


def _elapsed(name: str, state: PipelineState) -> float:
    """Seconds since the current stage started.

    `state.timings[name]` is the authoritative duration, but the runner writes
    it only after the stage returns — and an adapter logs while still inside
    the stage. Reading `timings` there reported `0.00s` for every stage, which
    is what the route's real per-stage durations degraded into when this
    sequence moved out of `extract()`. So the runner publishes its start
    reading and the adapter measures against that.
    """
    if state.stage_started_at is not None:
        return perf_counter() - state.stage_started_at
    return state.timings.get(name, 0.0)


def _log(name: str, template: str, *args: object, state: PipelineState) -> None:
    """Reproduce the route's per-stage log line, including its timing field."""
    logger.info(f"{name}: {template} in %.2fs", *args, _elapsed(name, state))


# ---- runtime gates, shared by several chains -----------------------------


def _is_llm(state: PipelineState) -> bool:
    return state.runtime == AgentRuntime.LLM


def _is_llm_or_hybrid(state: PipelineState) -> bool:
    """LLM *or* hybrid.

    Stages gated this way cost an LLM call, so they are gated on the runtime as
    well as their own flag: rule-based stays LLM-free even with the flag on, and
    that guarantee is what makes rule-based runs reproducible.
    """
    return state.runtime in (AgentRuntime.LLM, AgentRuntime.HYBRID)


# --------------------------------------------------------------------------
# reconcile chain — the head of the post-agent sequence
#
# `buckets` -> `reconciled`. This is where the payload type changes, which is
# why it is its own chain rather than part of the resolver one: everything
# after it reads `state.reconciled`.
# --------------------------------------------------------------------------


def _permissive_validation(state: PipelineState) -> None:
    """Log-only stage.

    The actual per-entity permissive validation happens inside the agents (via
    `validate_permissive`); by the time the buckets reach here the work is
    done. The route still logged a stage line with the entity count, and
    operators use it as the sequence's starting marker, so it stays a stage
    rather than becoming a stray log call.
    """
    count = sum(len(bucket) for bucket in state.buckets.values())
    state.extras["permissive_entity_count"] = count
    logger.info("permissive_validation: entity_count=%d", count)


async def _llm_dedup(state: PipelineState) -> None:
    from git_metadata_extractor.pipeline.stages import (  # noqa: PLC0415
        run_llm_dedup_stage,
    )

    state.extras["llm_dedup_executed"] = True
    result = await run_llm_dedup_stage(
        typed_entity_buckets=state.buckets,
        source_url=state.source_url,
        detected_type=state.detected_type,
        providers=state.providers,
        pipeline_outputs=state.pipeline_outputs,
        initial_context=state.gathered_context,
        max_concurrency=state.max_concurrent_agents,
    )
    state.buckets = result.typed_entity_buckets
    _log(
        "llm_dedup",
        "accepted=%d rejected=%d remap=%d",
        result.accepted_cluster_count,
        result.rejected_cluster_count,
        result.remap_count,
        state=state,
    )
    state.warn_all(result.warnings)


def _reconcile_entities(state: PipelineState) -> None:
    from git_metadata_extractor.pipeline.stages import (  # noqa: PLC0415
        reconcile_entities,
    )

    reconciled = reconcile_entities(state.buckets)
    state.reconciled = reconciled
    entities = reconciled.entities
    _log(
        "reconciliation",
        "persons=%d orgs=%d repos=%d articles=%d memberships=%d contributions=%d",
        len(entities.get("persons", [])),
        len(entities.get("organizations", [])),
        len(entities.get("repositories", [])),
        len(entities.get("articles", [])),
        len(reconciled.memberships),
        len(reconciled.contributions),
        state=state,
    )
    state.warn_all(reconciled.link_warnings)


RECONCILE_CHAIN: list[Stage] = [
    Stage("permissive_validation", _permissive_validation, fail_open=False),
    Stage("llm_dedup", _llm_dedup, applies=_is_llm, fail_open=True),
    # Fail-closed, as in the route: a graph that cannot be reconciled has no
    # meaningful partial form, so the request becomes a 500 rather than a
    # response missing every id.
    Stage("reconciliation", _reconcile_entities, fail_open=False),
]


# --------------------------------------------------------------------------
# ROR resolver chain
#
# Four stages that run between reconciliation and the critic, each trying to
# attach `schema:affiliation` to persons the previous one missed, then a final
# weaker pass over placeholder organizations. All four operate on `reconciled`
# and all four were already fail-open in the route: a ROR RAG that is down
# degrades the graph rather than failing the request.
#
# Ordering is load-bearing and matches the route: the Person-side resolvers run
# strongest-signal-first (`_company`, then `_bio`/`_blog`, then one LLM call
# per still-unresolved person), and the placeholder-organization pass runs last
# because it is strictly weaker than all three.
# --------------------------------------------------------------------------


def _ror_rag(state: PipelineState) -> object | None:
    return getattr(state.providers, "ror_rag", None)


async def _resolve_company_to_ror(state: PipelineState) -> None:
    result = await run_resolve_company_to_ror_stage(
        reconciled=state.reconciled,
        provider=_ror_rag(state),
        github_provider=getattr(state.providers, "github", None),
    )
    _log(
        "resolve_company_to_ror",
        "persons_examined=%d persons_resolved=%d "
        "memberships=%d organizations=%d queries=%d accepted=%d",
        result.persons_examined,
        result.persons_resolved,
        result.memberships_created,
        result.organizations_created,
        result.queries_attempted,
        result.queries_accepted,
        state=state,
    )


async def _resolve_bio_to_ror(state: PipelineState) -> None:
    result = await run_resolve_bio_to_ror_stage(
        reconciled=state.reconciled,
        provider=_ror_rag(state),
    )
    _log(
        "resolve_bio_to_ror",
        "persons_examined=%d persons_resolved=%d "
        "memberships=%d organizations=%d candidates=%d queries=%d accepted=%d",
        result.persons_examined,
        result.persons_resolved,
        result.memberships_created,
        result.organizations_created,
        result.candidates_extracted,
        result.queries_attempted,
        result.queries_accepted,
        state=state,
    )


async def _resolve_bio_to_ror_llm(state: PipelineState) -> None:
    result = await run_resolve_bio_to_ror_llm_stage(
        reconciled=state.reconciled,
        provider=_ror_rag(state),
    )
    _log(
        "resolve_bio_to_ror_llm",
        "persons_examined=%d called=%d resolved=%d failed=%d "
        "memberships=%d organizations=%d",
        result.persons_examined,
        result.persons_called,
        result.persons_resolved,
        result.persons_failed,
        result.memberships_created,
        result.organizations_created,
        state=state,
    )
    # The only one of the four that surfaces warnings of its own.
    state.warn_all(result.warnings)


async def _resolve_placeholder_orgs_to_ror(state: PipelineState) -> None:
    result = await run_resolve_placeholder_orgs_to_ror_stage(
        reconciled=state.reconciled,
        provider=_ror_rag(state),
    )
    _log(
        "resolve_placeholder_orgs_to_ror",
        "examined=%d resolved=%d memberships_rewritten=%d queries=%d accepted=%d",
        result.placeholders_examined,
        result.placeholders_resolved,
        result.memberships_rewritten,
        result.queries_attempted,
        result.queries_accepted,
        state=state,
    )


def _gate(name: str) -> StageGate:
    """A stage gate reading one of the API layer's env flags.

    The import is deferred on purpose. `api/__init__` imports `api/extract`,
    which imports this module, so importing `api._helpers` at module scope is a
    cycle — and the direction of that cycle is the tell: these flags read
    process configuration, not requests, so they belong in `config.py` rather
    than in the HTTP layer. Relocating them is its own change; this phase moves
    the sequence without touching what the stages read.
    """

    def gate(_state: PipelineState) -> bool:
        from git_metadata_extractor.api import _helpers  # noqa: PLC0415

        return bool(getattr(_helpers, name)())

    return gate


RESOLVER_CHAIN: list[Stage] = [
    Stage(
        "resolve_company_to_ror",
        _resolve_company_to_ror,
        applies=_gate("_resolve_company_to_ror_enabled"),
        fail_open=True,
    ),
    Stage(
        "resolve_bio_to_ror",
        _resolve_bio_to_ror,
        applies=_gate("_resolve_bio_to_ror_enabled"),
        fail_open=True,
    ),
    Stage(
        "resolve_bio_to_ror_llm",
        _resolve_bio_to_ror_llm,
        applies=lambda s: _is_llm_or_hybrid(s)
        and _gate("_resolve_bio_to_ror_llm_enabled")(s),
        fail_open=True,
    ),
    Stage(
        "resolve_placeholder_orgs_to_ror",
        _resolve_placeholder_orgs_to_ror,
        applies=_gate("_resolve_placeholder_orgs_to_ror_enabled"),
        fail_open=True,
    ),
]


# --------------------------------------------------------------------------
# refinement chain
#
# Two mutually exclusive LLM stages that rewrite `reconciled` in place after
# the resolvers: the critic (LLM runtime, drops entities it judges
# unsupported) and the hybrid refiner (hybrid runtime, patches whitelisted
# fields). Each is gated to one runtime, so at most one ever runs.
# --------------------------------------------------------------------------


def _critic_applies(state: PipelineState) -> bool:
    """LLM runtime *and* the pruning flag.

    Logs the flag-off case at INFO, which is unusual for a gate but is what the
    route did: operators rely on seeing that the critic was deliberately
    skipped rather than silently absent. `run_pipeline` catches gate
    exceptions, so logging here cannot affect the run.
    """
    if state.runtime != AgentRuntime.LLM:
        return False
    if not _gate("_should_apply_critic_pruning")(state):
        logger.info(
            "%s: skipped (V2_APPLY_CRITIC_PRUNING=false — entities preserved)",
            "llm_critic",
        )
        return False
    return True


async def _llm_critic(state: PipelineState) -> None:
    from git_metadata_extractor.pipeline.stages import (  # noqa: PLC0415
        run_llm_critic_stage,
    )

    # Set before the call, as the route did: the response reports that the
    # critic ran even when it then raised and failed open.
    state.extras["llm_critic_executed"] = True
    result = await run_llm_critic_stage(
        reconciled=state.reconciled,
        source_url=state.source_url,
        detected_type=state.detected_type,
        providers=state.providers,
        initial_context=state.gathered_context,
        pipeline_outputs=state.pipeline_outputs,
        max_concurrency=state.max_concurrent_agents,
        cache=state.cache,
    )
    state.reconciled = result.reconciled
    state.extras["critic_pruned_excluded_entities"] = result.pruned_excluded_entities
    _log(
        "llm_critic",
        "proposed_drop=%d applied_drop=%d protected_roots=%d",
        result.applied.get("proposed_drop_count", 0),
        result.applied.get("applied_drop_count", 0),
        len(result.applied.get("protected_root_ids", [])),
        state=state,
    )
    state.warn_all(result.warnings)


def _refiner_applies(state: PipelineState) -> bool:
    if state.runtime != AgentRuntime.HYBRID:
        return False
    from git_metadata_extractor.pipeline.stages.refine_with_llm import (  # noqa: PLC0415
        is_enabled,
    )

    return bool(is_enabled())


async def _refine_with_llm(state: PipelineState) -> None:
    from git_metadata_extractor.pipeline.stages import (  # noqa: PLC0415
        run_refine_with_llm_stage,
    )

    result = await run_refine_with_llm_stage(
        reconciled=state.reconciled,
        gathered_context=state.gathered_context,
        epfl_graph_provider=getattr(state.providers, "epfl_graph_rag", None),
        providers=state.providers,
        max_concurrency=state.max_concurrent_agents,
    )
    state.reconciled = result.reconciled
    _log(
        "refine_with_llm",
        "refined=%d skipped=%d failed=%d",
        result.refined_count,
        result.skipped_count,
        result.failed_count,
        state=state,
    )
    state.warn_all(result.warnings)


REFINEMENT_CHAIN: list[Stage] = [
    Stage("llm_critic", _llm_critic, applies=_critic_applies, fail_open=True),
    Stage("refine_with_llm", _refine_with_llm, applies=_refiner_applies, fail_open=True),
]


# --------------------------------------------------------------------------
# output chain — `reconciled` becomes `assembled`
#
# strict_validation -> assemble_output -> critic exclusions -> link_veracity.
# The three helpers below moved here from `api/extract.py`: they are graph
# logic that happened to live in the HTTP module, and the stages cannot reach
# back into `api` without a cycle.
# --------------------------------------------------------------------------


def _reconciled_entity_payloads(
    reconciled: Any,
) -> list[tuple[str, dict[str, Any]]]:
    payloads: list[tuple[str, dict[str, Any]]] = []
    entities = reconciled.entities
    for entity_type, bucket in (
        ("person", entities.get("persons", [])),
        ("organization", entities.get("organizations", [])),
        ("repository", entities.get("repositories", [])),
        ("article", entities.get("articles", [])),
    ):
        payloads.extend(
            (entity_type, entity) for entity in bucket if isinstance(entity, dict)
        )
    payloads.extend(
        ("membership", m) for m in reconciled.memberships if isinstance(m, dict)
    )
    payloads.extend(
        ("contribution", c) for c in reconciled.contributions if isinstance(c, dict)
    )
    return payloads


def _root_entity_type(detected_type: str) -> str:
    """`user` is the URL kind; `person` is the entity type it produces."""
    return "person" if detected_type == "user" else detected_type


def _rootless_assembled_output(
    *,
    reconciled: Any,
    strict_batch: Any,
    root_warning: str,
) -> AssembledOutput:
    """Assemble without a root when the root entity could not be identified.

    Distinct from the 422 path: a *missing* root still yields a usable graph of
    related entities, whereas a root that fails strict validation is a request
    error.
    """
    related_entities = [
        deepcopy(payload)
        for _, payload in strict_batch.valid_entities
        if isinstance(payload, dict)
    ]

    excluded_entities: list[dict[str, Any]] = []
    warnings = [*reconciled.link_warnings, root_warning]
    for entity_type, payload, validation in strict_batch.invalid_entities:
        if not isinstance(payload, dict):
            continue
        reason = list(validation.errors)
        excluded_entities.append(
            {
                "entity_type": entity_type,
                "entity": deepcopy(payload),
                "reason": reason,
            },
        )
        warnings.append(
            f"Excluded {entity_type} entity '{payload.get('id')}' due to strict "
            f"validation errors: {reason}",
        )

    return AssembledOutput(
        root_entity=None,
        related_entities=related_entities,
        excluded_entities=excluded_entities,
        warnings=warnings,
    )


def _strict_validation(state: PipelineState) -> None:
    from git_metadata_extractor.validation.schema_validation import (  # noqa: PLC0415
        StrictSchemaValidator,
    )

    batch = StrictSchemaValidator().validate_batch(
        _reconciled_entity_payloads(state.reconciled),
    )
    state.extras["strict_batch"] = batch
    _log(
        "strict_validation",
        "valid=%d invalid=%d",
        len(batch.valid_entities),
        len(batch.invalid_entities),
        state=state,
    )
    for warning in batch.warnings:
        state.warn(f"Strict validation: {warning}")


def _assemble_output(state: PipelineState) -> None:
    from git_metadata_extractor.pipeline.stages import (  # noqa: PLC0415
        assemble_output,
    )
    from git_metadata_extractor.pipeline.stages.output_assembly import (  # noqa: PLC0415
        RootEntityValidationError,
    )

    batch = state.extras["strict_batch"]
    try:
        state.assembled = assemble_output(
            state.reconciled,
            batch,
            root_entity_type=_root_entity_type(state.detected_type),
        )
    except RootEntityValidationError:
        # The route turns this into a 422 with the per-field errors. Let it
        # through unchanged — this stage is fail-closed, so `run_pipeline`
        # wraps it in StageError and the call site re-raises the cause.
        raise
    except ValueError as exc:
        logger.warning("output_assembly: rootless output — %s", exc)
        state.assembled = _rootless_assembled_output(
            reconciled=state.reconciled,
            strict_batch=batch,
            root_warning=str(exc),
        )
    _log(
        "output_assembly",
        "related=%d excluded=%d warnings=%d",
        len(state.assembled.related_entities),
        len(state.assembled.excluded_entities),
        len(state.assembled.warnings),
        state=state,
    )


def _apply_critic_exclusions(state: PipelineState) -> None:
    """Fold the critic's dropped entities into the assembled output.

    Not a pipeline stage in the route — it was an `if` block — but it belongs
    in the sequence: it is the only place the critic's decision reaches the
    response, and it has to happen after assembly.
    """
    pruned = state.extras.get("critic_pruned_excluded_entities") or []
    for excluded in pruned:
        if not isinstance(excluded, dict):
            continue
        state.assembled.excluded_entities.append(deepcopy(excluded))
        payload = excluded.get("entity")
        entity_id = payload.get("id") if isinstance(payload, dict) else None
        state.assembled.warnings.append(
            f"Excluded {excluded.get('entity_type', 'entity')} entity "
            f"'{entity_id}' due to critic pruning",
        )


def _propagate_assembled_warnings(state: PipelineState) -> None:
    state.warn_all(state.assembled.warnings)


def _link_veracity_applies(state: PipelineState) -> bool:
    """LLM-only, and separately flag-gated within LLM mode.

    Both skip reasons log at INFO because operators distinguish them: one is
    inherent to the runtime, the other is a deliberate opt-out for batch runs.
    """
    if state.runtime != AgentRuntime.LLM:
        logger.info(
            "link_veracity: skipped (agent_runtime=%s — link veracity is LLM-only)",
            state.runtime.value,
        )
        return False
    if not _gate("_is_link_veracity_enabled")(state):
        logger.info("link_veracity: skipped (V2_LINK_VERACITY_ENABLED=false)")
        return False
    return True


async def _link_veracity(state: PipelineState) -> None:
    from git_metadata_extractor.pipeline.stages import (  # noqa: PLC0415
        apply_link_pruning_to_assembled_output,
        promote_failed_id_entities,
        run_link_veracity_stage,
    )

    entities: list[dict[str, Any]] = []
    if isinstance(state.assembled.root_entity, dict):
        entities.append(deepcopy(state.assembled.root_entity))
    entities.extend(
        deepcopy(entity)
        for entity in state.assembled.related_entities
        if isinstance(entity, dict)
    )

    result = await run_link_veracity_stage(
        entities=entities,
        source_url=state.source_url,
        providers=state.providers,
        max_concurrency=state.max_concurrent_agents,
        cache=state.cache,
    )
    state.extras["veracity_records"] = result.records
    _log(
        "link_veracity",
        "checked=%d supported=%d unsupported=%d failed=%d invalid_links=%d",
        result.checked_count,
        result.supported_count,
        result.unsupported_count,
        result.failed_count,
        len(result.invalid_links),
        state=state,
    )
    state.warn(
        "Link veracity summary: "
        f"checked={result.checked_count}, "
        f"supported={result.supported_count}, "
        f"unsupported={result.unsupported_count}, "
        f"failed={result.failed_count}",
    )
    state.warn_all(result.warnings)

    invalid_links = set(result.invalid_links)
    if not invalid_links:
        return

    state.assembled, id_rewrites, promotion_warnings = promote_failed_id_entities(
        assembled=state.assembled,
        invalid_links=invalid_links,
    )
    state.warn_all(promotion_warnings)

    entity_link_map = result.entity_link_map
    if id_rewrites:
        logger.info(
            "link_veracity: promoted %d entity id(s) past failed url(s)",
            len(id_rewrites),
        )
        # The rewritten entities now expose their new id, so the old urls must
        # leave `invalid_links` or pruning would delete what promotion saved.
        invalid_links = invalid_links - set(id_rewrites)
        rebuilt: dict[str, list[str]] = {}
        for old_id, links in result.entity_link_map.items():
            rebuilt.setdefault(id_rewrites.get(old_id, old_id), []).extend(links)
        entity_link_map = rebuilt

    if invalid_links:
        state.assembled, pruning_warnings = apply_link_pruning_to_assembled_output(
            assembled=state.assembled,
            invalid_links=invalid_links,
            entity_link_map=entity_link_map,
            article_identifier_link_map=result.article_identifier_link_map,
        )
        state.warn_all(pruning_warnings)


def _mark_link_veracity_start(state: PipelineState) -> None:
    """Stamp the clock `link_veracity_seconds` is measured from.

    Set **unconditionally**, before the runtime and flag gates, because that is
    where the route set it: the reported figure therefore covers link-veracity
    plus the two stages after it, and is non-zero even when link-veracity is
    skipped entirely. Preserved rather than corrected — see
    `_record_link_veracity_seconds`.
    """
    state.extras["link_veracity_started_at"] = perf_counter()


OUTPUT_CHAIN: list[Stage] = [
    Stage("strict_validation", _strict_validation, fail_open=False),
    Stage("output_assembly", _assemble_output, fail_open=False),
    Stage("critic_exclusions", _apply_critic_exclusions, fail_open=False),
    Stage("assembled_warnings", _propagate_assembled_warnings, fail_open=False),
    Stage("link_veracity_start", _mark_link_veracity_start, fail_open=False),
    Stage(
        "link_veracity",
        _link_veracity,
        applies=_link_veracity_applies,
        fail_open=True,
    ),
]


# --------------------------------------------------------------------------
# payload chain — `assembled` becomes `payload`
#
# Discipline tagging, concept tagging, JSON-LD serialisation, SHACL gate.
# This is where the sequence stops: everything after it in the route builds the
# HTTP response (`V2JSONLDOutput`, the stage list, `compute_stats`), which is
# the API layer's job, not the graph pipeline's.
# --------------------------------------------------------------------------


def _repository_context(state: PipelineState) -> dict[str, Any] | None:
    context = state.gathered_context
    repository = context.get("repository") if isinstance(context, dict) else None
    return repository if isinstance(repository, dict) else None


def _is_repository_root(state: PipelineState) -> bool:
    return state.detected_type == "repository"


async def _tag_rule_based_disciplines(state: PipelineState) -> None:
    from git_metadata_extractor.pipeline.stages import (  # noqa: PLC0415
        tag_rule_based_disciplines,
    )

    readme_text: str | None = None
    github_description: str | None = None
    repository = _repository_context(state)
    if repository is not None:
        candidate = repository.get("readme_content")
        if isinstance(candidate, str):
            readme_text = candidate
        # The GitHub REST `description` is the repo's one-line pitch, far more
        # discriminative for discipline matching than the first 4k of a README
        # (often badge soup).
        metadata = repository.get("metadata")
        if isinstance(metadata, dict):
            description = metadata.get("description")
            if isinstance(description, str) and description.strip():
                github_description = description.strip()

    state.assembled, produced = await tag_rule_based_disciplines(
        state.assembled,
        readme_text=readme_text,
        github_description=github_description,
    )
    _log(
        "rule_based_disciplines",
        "emitted=%d",
        sum(1 for w in produced if "Inferred pulse:discipline" in w),
        state=state,
    )
    state.warn_all(produced)


def _concept_tagging_applies(state: PipelineState) -> bool:
    if not _is_repository_root(state):
        return False
    from git_metadata_extractor.pipeline.stages.concept_tagging import (  # noqa: PLC0415
        is_enabled,
    )

    return bool(is_enabled())


async def _concept_tagging(state: PipelineState) -> None:
    from git_metadata_extractor.pipeline.stages import (  # noqa: PLC0415
        run_concept_tagging_stage,
    )
    from git_metadata_extractor.pipeline.stages.concept_tagging import (  # noqa: PLC0415
        resolve_backend,
        resolve_epfl_min_score,
        resolve_related_enrichment,
    )

    repository = _repository_context(state)
    readme_text = repository.get("readme_content") if repository else None
    backend = resolve_backend()
    tagged_root, result = await run_concept_tagging_stage(
        root_entity=state.assembled.root_entity,
        readme_text=readme_text if isinstance(readme_text, str) else None,
        backend=backend,
        epfl_min_score=resolve_epfl_min_score(),
        enable_related_openalex=resolve_related_enrichment(),
    )
    state.assembled.root_entity = tagged_root
    _log(
        "concept_tagging",
        "backend=%s keywords=%d concepts=%d disciplines=%d",
        result.backend,
        len(result.keywords),
        len(result.concepts),
        len(result.disciplines),
        state=state,
    )
    state.warn_all(result.warnings)


def _build_jsonld(state: PipelineState) -> None:
    from git_metadata_extractor.pipeline.stages import (  # noqa: PLC0415
        build_jsonld_output,
    )

    state.payload = build_jsonld_output(
        assembled=state.assembled,
        jsonld_context=state.jsonld_context,
        include_internal_fields=state.include_internal_fields,
    )
    nodes = state.payload.get("@graph")
    _log(
        "jsonld_build",
        "entities=%d context_terms=%d",
        len(nodes) if isinstance(nodes, list) else 0,
        len(state.jsonld_context),
        state=state,
    )


def _shacl_gate(state: PipelineState) -> None:
    """Warning-only: violations become response warnings, never a failure.

    Producing a SHACL-clean graph is the upstream stages' job; this gate
    reports. Turning it enforcing is plan phase 8.
    """
    from git_metadata_extractor.api import _helpers  # noqa: PLC0415
    from git_metadata_extractor.validation import (  # noqa: PLC0415
        SHACLValidator,
        canonical_shapes_available,
        load_canonical_shapes_graph,
        load_ontology_shapes_graph,
    )
    from git_metadata_extractor.validation.shacl_validation import (  # noqa: PLC0415
        SHACLRuntimeUnavailableError,
    )

    # Validate against the shapes that describe the graph actually built. Once
    # `canonical_projection` has run the payload is v3, and the v2.1.2 bundle
    # would report every profile node as an unknown class — noise, not signal.
    projected = bool(state.timings.get("canonical_projection") is not None)
    use_canonical = projected and canonical_shapes_available()
    shapes = load_canonical_shapes_graph() if use_canonical else load_ontology_shapes_graph()

    data_graph = _helpers._jsonld_to_graph(state.payload)  # noqa: SLF001
    if data_graph is None:
        logger.warning(
            "shacl_gate: skipped — unable to parse assembled graph payload",
        )
        state.warn("SHACL validation skipped: unable to parse assembled graph payload")
        return

    try:
        result = SHACLValidator().validate_graph(data_graph, shapes)
    except SHACLRuntimeUnavailableError as exc:
        # pyshacl missing or unusable is an environment problem, not a graph
        # problem, so it must not read as a conformance failure.
        logger.warning("shacl_gate: skipped — %s", exc)
        state.warn(str(exc))
        return
    except Exception as exc:
        logger.exception("shacl_gate failed")
        state.warn(f"SHACL validation failed: {exc}")
        return

    for violation in result.violations:
        state.warn(
            "SHACL violation: "
            f"focus={violation.get('focusNode')}, "
            f"path={violation.get('path')}, "
            f"message={violation.get('message')}",
        )
    for shacl_warning in result.warnings:
        state.warn(
            "SHACL warning: "
            f"focus={shacl_warning.get('focusNode')}, "
            f"path={shacl_warning.get('path')}, "
            f"message={shacl_warning.get('message')}",
        )
    logger.info(
        "shacl_gate: conforms=%s violations=%d warnings=%d shapes=%s",
        result.conforms,
        len(result.violations),
        len(result.warnings),
        "canonical-v3" if use_canonical else "v2.1.2",
    )


def _record_extraction_run(state: PipelineState) -> None:
    """Describe this run as a `pulse:ExtractionRun`, beside the graph.

    Last in the chain because `prov:endedAtTime` should cover everything that
    happened to the graph, the SHACL gate included.

    Deliberately not added to `state.payload["@graph"]`: the four-layer model
    puts runs in the substrate/provenance layer, and `graph:canonical` holds
    what was produced rather than how. The route returns it as a sibling of
    `output`, so the corpus signature — which reads `output["@graph"]` — is
    unaffected.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    from git_metadata_extractor.api._helpers import (  # noqa: PLC0415
        PACKAGE_NAME,
        PACKAGE_VERSION,
    )
    from git_metadata_extractor.pipeline.stages.extraction_run import (  # noqa: PLC0415
        build_extraction_run,
    )

    started = state.started_at or datetime.now(timezone.utc)
    state.extras["extraction_run"] = build_extraction_run(
        run_id=state.run_id,
        seeds=[state.source_url],
        started_at=started,
        package_name=PACKAGE_NAME,
        package_version=PACKAGE_VERSION,
    )
    logger.info(
        "extraction_run: id=%s seeds=1 agent=%s",
        state.run_id,
        PACKAGE_VERSION,
    )


def _project_canonical(state: PipelineState) -> None:
    """Replace the built graph with its v3 canonical projection.

    Runs after `jsonld_build`, and *on* its output rather than instead of it:
    `build_jsonld_output` reads the v2 context to decide which values serialise
    as `{"@id": ...}` references and it normalises node ids, so it still
    produces the intermediate this projects from. What changes is which graph
    reaches the caller, and under which context.

    The `@context` becomes the generated one, because the graph it describes is
    now v3. The v2 context stays internal, describing the intermediate.

    `excluded_entities` passes through unprojected on purpose: it explains why
    entities were *dropped* during v2 validation, which is diagnostic output
    about a v2 process rather than canonical graph data.
    """
    from git_metadata_extractor.pipeline.stages.canonical_projection import (  # noqa: PLC0415
        project_canonical,
    )
    from git_metadata_extractor.schema import load_generated_context  # noqa: PLC0415

    nodes = state.payload.get("@graph")
    if not isinstance(nodes, list):
        return
    projected = project_canonical(nodes)
    state.payload["@graph"] = projected["@graph"]
    state.payload["@context"] = load_generated_context()
    _log(
        "canonical_projection",
        "in=%d out=%d",
        len(nodes),
        len(projected["@graph"]),
        state=state,
    )


PAYLOAD_CHAIN: list[Stage] = [
    Stage(
        "rule_based_disciplines",
        _tag_rule_based_disciplines,
        applies=_is_repository_root,
        fail_open=False,
    ),
    Stage(
        "concept_tagging",
        _concept_tagging,
        applies=_concept_tagging_applies,
        fail_open=True,
    ),
    Stage("jsonld_build", _build_jsonld, fail_open=False),
    # Before the gate, not after: the gate must validate the graph the caller
    # actually receives. Fail-closed, because a failed projection would serve
    # the v2 graph under the v3 context — a document that describes itself
    # wrongly, which is worse than an error.
    Stage(
        "canonical_projection",
        _project_canonical,
        applies=_gate("_canonical_output_enabled"),
        fail_open=False,
    ),
    # The gate handles its own failure modes and never raises, so fail_open is
    # moot here; declared False to match the route, where it was unwrapped.
    Stage("shacl_gate", _shacl_gate, fail_open=False),
    # Fail-open: a malformed run descriptor must not cost the caller a graph
    # that is otherwise complete.
    Stage("extraction_run", _record_extraction_run, fail_open=True),
]


# --------------------------------------------------------------------------
# assembled-output chain
#
# Every stage below takes an AssembledOutput and returns
# (AssembledOutput, warnings). That uniformity is why this segment moved
# first — the earlier segments still thread three other payload types.
# --------------------------------------------------------------------------


def _validate_articles(state: PipelineState) -> None:
    state.assembled, produced = validate_articles(
        state.assembled,
        veracity_records=state.extras.get("veracity_records") or [],
    )
    _log("validate_articles", "warnings=%d", len(produced), state=state)
    state.warn_all(produced)


def _author_class_validation(state: PipelineState) -> None:
    state.assembled, produced = validate_author_classes(state.assembled)
    _log("author_class_validation", "pruned=%d", len(produced), state=state)
    state.warn_all(produced)


def _record_link_veracity_seconds(state: PipelineState) -> None:
    """Not a stage — a measurement the route took at this exact point.

    It brackets link-veracity *plus* the two stages above it. That is almost
    certainly unintentional, but it is what the current stats report, so the
    position is preserved rather than corrected here.
    """
    started_at = state.extras.get("link_veracity_started_at")
    if started_at is not None:
        from time import perf_counter  # noqa: PLC0415

        state.extras["link_veracity_seconds"] = perf_counter() - started_at


def _ownership_check(state: PipelineState) -> None:
    state.assembled, produced = validate_ownership(state.assembled)
    _log("ownership_check", "dropped=%d", len(produced), state=state)
    state.warn_all(produced)


def _owner_inference(state: PipelineState) -> None:
    # Runs BEFORE prune_dangling_refs so it can materialise minimal Person
    # stubs for github owners with no matching entity — prune would clear
    # `pulse:ownedBy` first and the stub opportunity would be lost.
    state.assembled, produced = infer_owners(state.assembled)
    _log("owner_inference", "stamped=%d", len(produced), state=state)
    state.warn_all(produced)


def _inverse_consistency(state: PipelineState) -> None:
    # Second pass: `infer_owners` indexes by github handle and may have
    # stamped `pulse:owns` on the ROR-side Org as well as the github-handle
    # one. Re-running the check drops whichever entry disagrees with the
    # repo's actual `pulse:ownedBy`.
    state.assembled, produced = validate_ownership(state.assembled)
    _log("inverse_consistency", "dropped=%d", len(produced), state=state)
    state.warn_all(produced)


def _prune_dangling_refs(state: PipelineState) -> None:
    state.assembled, produced = prune_dangling_refs(state.assembled)
    _log("prune_dangling_refs", "actions=%d", len(produced), state=state)
    state.warn_all(produced)


async def _github_handle_parents(state: PipelineState) -> None:
    # Fuzzy-searches ROR for parents of every github-only org. The github org
    # stays standalone; ROR matches are added and the best becomes its
    # `unitOf` parent. Runs before org_relationships so the LLM sees the new
    # ROR entities, and before infer_org_units so the token-overlap fallback
    # sees the broader graph.
    #
    # In LLM/hybrid an agent picks the single correct parent (or declines).
    # rule_based gets no selector, which is what keeps that mode LLM-free.
    selector = None
    if state.runtime != AgentRuntime.RULE_BASED:
        from git_metadata_extractor.agents.llm.refiners.ror_parent.agent import (  # noqa: PLC0415
            RorParentSelectorAgent,
        )

        selector = RorParentSelectorAgent()

    state.assembled, produced = await infer_github_handle_parents(
        state.assembled,
        providers=state.providers,
        parent_selector=selector,
    )
    _log("github_handle_parents", "actions=%d", len(produced), state=state)
    state.warn_all(produced)


async def _org_relationships(state: PipelineState) -> None:
    state.assembled, produced = await run_org_relationships_stage(
        assembled=state.assembled,
        source_url=state.source_url,
        providers=state.providers,
    )
    _log("org_relationships", "edges=%d", len(produced), state=state)
    state.warn_all(produced)


def _org_unit_inference(state: PipelineState) -> None:
    state.assembled, produced = infer_org_units(state.assembled)
    _log("org_unit_inference", "stamped=%d", len(produced), state=state)
    state.warn_all(produced)


def _demote_github_props(state: PipelineState) -> None:
    state.assembled, produced = demote_github_props_to_units(state.assembled)
    _log("demote_github_props_to_units", "demoted=%d", len(produced), state=state)
    state.warn_all(produced)


def _fork_parent_stubs(state: PipelineState) -> None:
    # Minimal stubs so `pulse:isForkOf` satisfies SHACL
    # `sh:class schema:SoftwareSourceCode` without ingesting the upstream repo.
    state.assembled, produced = emit_fork_parent_stubs(state.assembled)
    _log("fork_parent_stubs", "emitted=%d", len(produced), state=state)
    state.warn_all(produced)


def _article_source_org(state: PipelineState) -> None:
    # Stamps `schema:sourceOrganization` on Articles lacking one when exactly
    # one Org had a confirmed member on the publication date. Refuses to guess
    # when ambiguous.
    state.assembled, produced = infer_article_source_organization(state.assembled)
    _log("article_source_org_inference", "stamped=%d", len(produced), state=state)
    state.warn_all(produced)


#: The assembled-output segment, in the order the route ran it.
ASSEMBLED_CHAIN: list[Stage] = [
    Stage("validate_articles", _validate_articles, fail_open=False),
    Stage("author_class_validation", _author_class_validation, fail_open=False),
    Stage("link_veracity_seconds", _record_link_veracity_seconds, fail_open=False),
    Stage("ownership_check", _ownership_check, fail_open=False),
    Stage("owner_inference", _owner_inference, fail_open=False),
    Stage("inverse_consistency", _inverse_consistency, fail_open=False),
    Stage("prune_dangling_refs", _prune_dangling_refs, fail_open=False),
    Stage("github_handle_parents", _github_handle_parents, fail_open=False),
    # The only stage the route wrapped in try/except.
    Stage("org_relationships", _org_relationships, applies=_is_llm, fail_open=True),
    Stage("org_unit_inference", _org_unit_inference, fail_open=False),
    Stage("demote_github_props_to_units", _demote_github_props, fail_open=False),
    Stage("fork_parent_stubs", _fork_parent_stubs, fail_open=False),
    Stage("article_source_org_inference", _article_source_org, fail_open=False),
]


# --------------------------------------------------------------------------
# reconciled chain
#
# Runs between reconciliation and strict validation, on ReconciledEntities
# rather than AssembledOutput.
# --------------------------------------------------------------------------


def _guarantee_repo_author(state: PipelineState) -> None:
    # KNOWN BUG salvage: when reconciliation drops unresolvable
    # `schema:author` references a repository can end up with an empty author
    # array, which strict validation rejects. Falls back to the github owner
    # when it is in the graph.
    #
    # The baseline corpus shows this firing 46 times and still leaving 46
    # `schema:author` SHACL violations behind, so the salvage does not always
    # succeed — see tests/v2/corpus/BASELINE_FINDINGS.md. Fixing that is plan
    # phase 6/8 work, not this move's.
    state.reconciled, produced = guarantee_repo_author(state.reconciled)
    _log("guarantee_repo_author", "salvaged=%d", len(produced), state=state)
    state.warn_all(produced)


def _validate_org_github_handles(state: PipelineState) -> None:
    # Checks `@handle`-style org names against GitHub before strict
    # validation, so a missing handle gets stamped or a hallucinated entity
    # gets dropped rather than silently failing `anyOf`.
    state.reconciled, produced = validate_org_github_handles(
        state.reconciled,
        state.providers,
    )
    _log("validate_org_github_handles", "actions=%d", len(produced), state=state)
    state.warn_all(produced)


#: The reconciled segment, in the order the route ran it.
RECONCILED_CHAIN: list[Stage] = [
    Stage("guarantee_repo_author", _guarantee_repo_author, fail_open=False),
    Stage("validate_org_github_handles", _validate_org_github_handles, fail_open=False),
]
