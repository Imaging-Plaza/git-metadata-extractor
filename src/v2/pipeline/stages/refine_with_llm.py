"""Hybrid-runtime LLM refinement stage.

Runs after `reconcile_entities`. For each Organization, Repository, and Person
in the reconciled graph, calls a typed LLM "refiner" that may propose a small
patch (whitelisted fields only). The whitelist is enforced here — fields
outside it are dropped and a warning is logged.

Determinístic fields (counts, stars/forks, identifiers, dates) are never
exposed to the refiner contract via the whitelist, so the LLM cannot
accidentally clobber them even when its model emits them.

Opt-in via ``agent_runtime=hybrid``. Each entity runs through its refiner
independently with bounded concurrency.
"""

from __future__ import annotations

import asyncio
import logging
import os
from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from src.v2.agents.llm.agent_tools.graph_neighbors import make_get_entity_neighbors_tool
from src.v2.agents.llm.refiners import (
    MembershipRefinerAgent,
    MembershipRefinerInput,
    OrganizationRefinerAgent,
    OrganizationRefinerInput,
    PersonRefinerAgent,
    PersonRefinerInput,
    RepositoryRefinerAgent,
    RepositoryRefinerInput,
)
from src.v2.agents.llm.runtime import LLMRuntimeError
from src.v2.ingest.providers.epfl_graph_rag import EpflGraphRagProvider
from src.v2.pipeline.stages.models import ReconciledEntities

logger = logging.getLogger(__name__)

ORG_PATCHABLE_FIELDS: frozenset[str] = frozenset({"pulse:OrganizationType"})
REPO_PATCHABLE_FIELDS: frozenset[str] = frozenset(
    {"pulse:discipline", "pulse:repositoryType"},
)
PERSON_PATCHABLE_FIELDS: frozenset[str] = frozenset({"schema:name"})
MEMBERSHIP_PATCHABLE_FIELDS: frozenset[str] = frozenset({"org:role"})

README_CONTEXT_MAX_CHARS = 1500
DEFAULT_MAX_CONCURRENCY = 4

EPFL_GRAPH_HITS_TOP_K = 8
EPFL_GRAPH_HIT_FIELDS: frozenset[str] = frozenset(
    {"category_id", "name", "depth", "parent_id", "wikipedia_url", "score"},
)


@dataclass(slots=True)
class RefineWithLLMResult:
    """Outcome of the refinement stage across all entity types."""

    reconciled: ReconciledEntities
    warnings: list[str] = field(default_factory=list)
    refined_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    by_type: dict[str, dict[str, int]] = field(default_factory=dict)


def is_enabled() -> bool:
    """Return True when the hybrid refiner stage is enabled (default true)."""

    raw = os.getenv("V2_HYBRID_REFINER_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


def _build_repo_context_summary(
    *,
    gathered_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(gathered_context, dict):
        return {}
    repository_context = gathered_context.get("repository")
    if not isinstance(repository_context, dict):
        return {}

    summary: dict[str, Any] = {}
    metadata = repository_context.get("metadata")
    if isinstance(metadata, dict):
        meta_summary = {
            key: metadata.get(key)
            for key in ("name", "full_name", "description", "owner")
            if metadata.get(key) is not None
        }
        if meta_summary:
            summary["metadata"] = meta_summary

    readme = repository_context.get("readme_content")
    if isinstance(readme, str) and readme:
        summary["readme_excerpt"] = readme[:README_CONTEXT_MAX_CHARS]

    return summary


async def _fetch_epfl_graph_hits(
    *,
    provider: EpflGraphRagProvider | None,
    repo_context_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run one EPFL Graph disciplines RAG search using README + description.

    Returns a thin list of `{category_id, name, depth, parent_id, wikipedia_url, score}`
    dicts (top-K) that the repository refiner uses to ground its discipline choice.
    Returns `[]` on any error / missing input — never raises.
    """

    if provider is None:
        return []

    metadata = repo_context_summary.get("metadata") or {}
    parts: list[str] = []
    name = metadata.get("name")
    description = metadata.get("description")
    readme = repo_context_summary.get("readme_excerpt")
    for value in (name, description, readme):
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    query = "\n\n".join(parts).strip()
    if not query:
        return []

    try:
        hits = await provider.search(query, top_k=EPFL_GRAPH_HITS_TOP_K, rerank=True)
    except Exception as exc:  # noqa: BLE001 — RAG availability is optional
        logger.warning("refine_with_llm: EPFL Graph RAG search failed — %s", exc)
        return []

    return [
        {key: hit.get(key) for key in EPFL_GRAPH_HIT_FIELDS if key in hit}
        for hit in hits
    ]


def _apply_patch(
    *,
    entity: dict[str, Any],
    patch: dict[str, Any],
    whitelist: frozenset[str],
    type_label: str,
) -> tuple[bool, list[str]]:
    """Merge `patch` into `entity` honouring the whitelist."""

    warnings: list[str] = []
    changed = False
    for key, value in patch.items():
        if key not in whitelist:
            warnings.append(
                f"refine_with_llm: dropped non-whitelisted field '{key}' "
                f"({type_label}={entity.get('schema:name')!r})",
            )
            continue
        current = entity.get(key)
        if current == value:
            continue
        entity[key] = value
        changed = True
    return changed, warnings


async def _run_refiner_safely(
    *,
    coro: Any,
    entity: dict[str, Any],
    type_label: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Wrap a refiner call so a single failure cannot kill the stage."""

    name = entity.get("schema:name") or entity.get("id", "?")
    try:
        patch = await coro
        return entity, patch, []
    except LLMRuntimeError as exc:
        warning = f"refine_with_llm: {type_label} refiner failed for {name!r}: {exc}"
        logger.warning(warning)
        return entity, {}, [warning]
    except Exception as exc:  # noqa: BLE001 — boundary for stage isolation
        warning = (
            f"refine_with_llm: unexpected error in {type_label} refiner for "
            f"{name!r}: {exc}"
        )
        logger.exception(warning)
        return entity, {}, [warning]


async def _refine_org(
    *,
    organization: dict[str, Any],
    refiner: OrganizationRefinerAgent,
    repo_context_summary: dict[str, Any],
    neighbors_tool: Any,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    async with semaphore:
        return await _run_refiner_safely(
            coro=refiner.run(
                refiner_input=OrganizationRefinerInput(
                    entity=deepcopy(organization),
                    repo_context_summary=repo_context_summary,
                ),
                tools=[neighbors_tool],
            ),
            entity=organization,
            type_label="organization",
        )


async def _refine_repo(
    *,
    repository: dict[str, Any],
    refiner: RepositoryRefinerAgent,
    repo_context_summary: dict[str, Any],
    neighbors_tool: Any,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    async with semaphore:
        return await _run_refiner_safely(
            coro=refiner.run(
                refiner_input=RepositoryRefinerInput(
                    entity=deepcopy(repository),
                    repo_context_summary=repo_context_summary,
                ),
                tools=[neighbors_tool],
            ),
            entity=repository,
            type_label="repository",
        )


async def _refine_person(
    *,
    person: dict[str, Any],
    refiner: PersonRefinerAgent,
    repo_context_summary: dict[str, Any],
    neighbors_tool: Any,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    async with semaphore:
        return await _run_refiner_safely(
            coro=refiner.run(
                refiner_input=PersonRefinerInput(
                    entity=deepcopy(person),
                    repo_context_summary=repo_context_summary,
                ),
                tools=[neighbors_tool],
            ),
            entity=person,
            type_label="person",
        )


async def _refine_membership(
    *,
    membership: dict[str, Any],
    refiner: MembershipRefinerAgent,
    organization_name: str | None,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    async with semaphore:
        return await _run_refiner_safely(
            coro=refiner.run(
                refiner_input=MembershipRefinerInput(
                    entity=deepcopy(membership),
                    organization_name=organization_name,
                ),
            ),
            entity=membership,
            type_label="membership",
        )


def _resolve_org_name_for_membership(
    *,
    membership: dict[str, Any],
    org_index: dict[str, str],
) -> str | None:
    org_ref = membership.get("org:organization")
    if isinstance(org_ref, dict):
        org_ref = org_ref.get("@id") or org_ref.get("id")
    if isinstance(org_ref, str):
        return org_index.get(org_ref)
    return None


def _gate_repo_type_patch(
    *,
    repository: dict[str, Any],
    patch: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Drop `pulse:repositoryType` from the patch when the current value is not `pulse:Other`.

    The prompt already tells the model to skip; this is a server-side safety net.
    """

    if "pulse:repositoryType" not in patch:
        return patch, []
    current = repository.get("pulse:repositoryType")
    if current == "pulse:Other":
        return patch, []
    pruned = {k: v for k, v in patch.items() if k != "pulse:repositoryType"}
    warning = (
        "refine_with_llm: dropped repository type rewrite "
        f"({repository.get('schema:name')!r}: current={current} → proposed={patch['pulse:repositoryType']}) — "
        "only `pulse:Other` may be reclassified"
    )
    return pruned, [warning]


async def run_refine_with_llm_stage(  # noqa: PLR0913, PLR0915
    *,
    reconciled: ReconciledEntities,
    gathered_context: dict[str, Any] | None,
    org_refiner: OrganizationRefinerAgent | None = None,
    repo_refiner: RepositoryRefinerAgent | None = None,
    person_refiner: PersonRefinerAgent | None = None,
    membership_refiner: MembershipRefinerAgent | None = None,
    epfl_graph_provider: EpflGraphRagProvider | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> RefineWithLLMResult:
    """Run the hybrid LLM refinement stage over reconciled entities.

    Mutates ``reconciled`` in place — the caller receives the same instance back.
    """

    organizations = reconciled.entities.get("organizations") or []
    repositories = reconciled.entities.get("repositories") or []
    persons = reconciled.entities.get("persons") or []
    memberships_with_role = [m for m in reconciled.memberships if m.get("org:role")]

    if not (organizations or repositories or persons or memberships_with_role):
        return RefineWithLLMResult(reconciled=reconciled)

    org_refiner = org_refiner or OrganizationRefinerAgent()
    repo_refiner = repo_refiner or RepositoryRefinerAgent()
    person_refiner = person_refiner or PersonRefinerAgent()
    membership_refiner = membership_refiner or MembershipRefinerAgent()

    repo_context_summary = _build_repo_context_summary(gathered_context=gathered_context)
    epfl_graph_hits = await _fetch_epfl_graph_hits(
        provider=epfl_graph_provider,
        repo_context_summary=repo_context_summary,
    )
    if epfl_graph_hits:
        logger.info(
            "refine_with_llm: EPFL Graph hits prefetched count=%d top_score=%.3f",
            len(epfl_graph_hits),
            epfl_graph_hits[0].get("score") or 0.0,
        )
    repo_context_summary_for_repo = (
        {**repo_context_summary, "epfl_graph_hits": epfl_graph_hits}
        if epfl_graph_hits
        else repo_context_summary
    )
    neighbors_tool = make_get_entity_neighbors_tool(
        entities=reconciled.entities,
        memberships=reconciled.memberships,
        contributions=reconciled.contributions,
    )
    semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    org_name_index = {
        org["id"]: org.get("schema:name", "")
        for org in organizations
        if isinstance(org.get("id"), str)
    }

    started_at = perf_counter()
    tasks: list[Any] = []
    routing: list[tuple[str, frozenset[str]]] = []

    for organization in organizations:
        tasks.append(
            _refine_org(
                organization=organization,
                refiner=org_refiner,
                repo_context_summary=repo_context_summary,
                neighbors_tool=neighbors_tool,
                semaphore=semaphore,
            ),
        )
        routing.append(("organization", ORG_PATCHABLE_FIELDS))

    for repository in repositories:
        tasks.append(
            _refine_repo(
                repository=repository,
                refiner=repo_refiner,
                repo_context_summary=repo_context_summary_for_repo,
                neighbors_tool=neighbors_tool,
                semaphore=semaphore,
            ),
        )
        routing.append(("repository", REPO_PATCHABLE_FIELDS))

    for person in persons:
        tasks.append(
            _refine_person(
                person=person,
                refiner=person_refiner,
                repo_context_summary=repo_context_summary,
                neighbors_tool=neighbors_tool,
                semaphore=semaphore,
            ),
        )
        routing.append(("person", PERSON_PATCHABLE_FIELDS))

    for membership in memberships_with_role:
        tasks.append(
            _refine_membership(
                membership=membership,
                refiner=membership_refiner,
                organization_name=_resolve_org_name_for_membership(
                    membership=membership,
                    org_index=org_name_index,
                ),
                semaphore=semaphore,
            ),
        )
        routing.append(("membership", MEMBERSHIP_PATCHABLE_FIELDS))

    results = await asyncio.gather(*tasks, return_exceptions=False)

    warnings: list[str] = []
    by_type: dict[str, dict[str, int]] = {
        "organization": {"refined": 0, "skipped": 0, "failed": 0},
        "repository": {"refined": 0, "skipped": 0, "failed": 0},
        "person": {"refined": 0, "skipped": 0, "failed": 0},
        "membership": {"refined": 0, "skipped": 0, "failed": 0},
    }
    for (type_label, whitelist), (entity, patch, run_warnings) in zip(
        routing,
        results,
        strict=True,
    ):
        warnings.extend(run_warnings)
        if run_warnings and not patch:
            by_type[type_label]["failed"] += 1
            continue
        if type_label == "repository":
            patch, gate_warnings = _gate_repo_type_patch(repository=entity, patch=patch)
            warnings.extend(gate_warnings)
        if not patch:
            by_type[type_label]["skipped"] += 1
            continue
        changed, merge_warnings = _apply_patch(
            entity=entity,
            patch=patch,
            whitelist=whitelist,
            type_label=type_label,
        )
        warnings.extend(merge_warnings)
        if changed:
            by_type[type_label]["refined"] += 1
        else:
            by_type[type_label]["skipped"] += 1

    refined_count = sum(stats["refined"] for stats in by_type.values())
    skipped_count = sum(stats["skipped"] for stats in by_type.values())
    failed_count = sum(stats["failed"] for stats in by_type.values())

    logger.info(
        "refine_with_llm: refined=%d skipped=%d failed=%d "
        "(orgs=%s repos=%s persons=%s memberships=%s) in %.2fs",
        refined_count,
        skipped_count,
        failed_count,
        by_type["organization"],
        by_type["repository"],
        by_type["person"],
        by_type["membership"],
        perf_counter() - started_at,
    )

    return RefineWithLLMResult(
        reconciled=reconciled,
        warnings=warnings,
        refined_count=refined_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        by_type=by_type,
    )


__all__ = [
    "MEMBERSHIP_PATCHABLE_FIELDS",
    "ORG_PATCHABLE_FIELDS",
    "PERSON_PATCHABLE_FIELDS",
    "REPO_PATCHABLE_FIELDS",
    "RefineWithLLMResult",
    "is_enabled",
    "run_refine_with_llm_stage",
]
