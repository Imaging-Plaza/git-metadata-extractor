"""Pipeline stage: LLM-decided `org:unitOf` / `org:hasUnit` edges.

Sits between the deterministic ownership stages and `build_jsonld_output`.
Asks an LLM to look at the **whole set of organizations** in the assembled
output and propose parent-child edges. The response is validated locally
(ids must exist, no self-loops, no cycles, one parent per child) before
the edges are stamped onto the entities.

The deterministic `infer_org_units` stage stays in place as a safety net
that runs after this; it only fills in when the LLM left a gap and the
name evidence is unambiguous.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.v2.agents.llm.org_relationships import LLMOrgRelationshipsAgentV2
from src.v2.agents.models import ProviderSet
from src.v2.pipeline.stages.models import AssembledOutput

ORGANIZATION_TYPE = "org:Organization"
UNIT_OF_KEY = "org:unitOf"
HAS_UNIT_KEY = "org:hasUnit"

# Fields the LLM gets per organization. We strip noise (uuid, derivations,
# timestamps) so the prompt stays focused on the identifiers and names that
# matter for hierarchy decisions.
_LLM_FIELDS: tuple[str, ...] = (
    "id",
    "schema:name",
    "schema:description",
    "pulse:OrganizationType",
    "pulse:githubOrganizationHandle",
    "pulse:ror",
    "pulse:infoscienceOrganizationIdentifier",
    "org:unitOf",
    "org:hasUnit",
)


def _organization_summary_for_prompt(entity: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in _LLM_FIELDS:
        value = entity.get(key)
        if value is None:
            continue
        summary[key] = value
    identifiers = entity.get("identifiers")
    if isinstance(identifiers, dict):
        for key in ("pulse:ror", "pulse:infoscienceOrganizationIdentifier"):
            value = identifiers.get(key)
            if isinstance(value, str) and value and key not in summary:
                summary[key] = value
    return summary


def _collect_organizations(
    assembled: AssembledOutput,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(assembled.root_entity, dict):
        candidates.append(assembled.root_entity)
    candidates.extend(
        entity for entity in assembled.related_entities if isinstance(entity, dict)
    )
    organizations = [
        entity for entity in candidates if entity.get("type") == ORGANIZATION_TYPE
    ]
    return candidates, organizations


def _set_unit_of(child: dict[str, Any], parent_id: str) -> bool:
    """Set `org:unitOf` on child only if currently empty. Returns True if set."""
    existing = child.get(UNIT_OF_KEY)
    if existing in (None, "", {}, []):
        child[UNIT_OF_KEY] = [parent_id]
        return True
    if isinstance(existing, list):
        for entry in existing:
            if (isinstance(entry, dict) and entry.get("@id") == parent_id) or entry == parent_id:
                return False
        return False
    if (isinstance(existing, dict) and existing.get("@id") == parent_id) or existing == parent_id:
        return False
    return False


def _add_to_has_unit(parent: dict[str, Any], child_id: str) -> bool:
    existing = parent.get(HAS_UNIT_KEY)
    if existing is None or existing == []:
        parent[HAS_UNIT_KEY] = [child_id]
        return True
    if not isinstance(existing, list):
        return False
    for entry in existing:
        if isinstance(entry, dict) and entry.get("@id") == child_id:
            return False
        if isinstance(entry, str) and entry == child_id:
            return False
    existing.append(child_id)
    return True


def _would_create_cycle(
    child_id: str,
    parent_id: str,
    edges: dict[str, list[str]],
) -> bool:
    """Return True if adding `child unitOf parent` produces a cycle.

    Walks up from `parent_id` along all existing parent edges (multi-parent
    DAG); if we encounter `child_id` along the way, the new edge closes a loop.
    """

    visited: set[str] = set()
    queue: list[str] = [parent_id]
    while queue:
        current = queue.pop()
        if current == child_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        queue.extend(edges.get(current) or [])
    return False


async def run_org_relationships_stage(
    *,
    assembled: AssembledOutput,
    source_url: str,
    providers: ProviderSet,
    agent: LLMOrgRelationshipsAgentV2 | None = None,
) -> tuple[AssembledOutput, list[str]]:
    """Run one LLM call to propose `org:unitOf` edges, then stamp the safe ones.

    Returns the updated assembled output and a list of human-readable warnings
    summarising every edge that was applied (and every proposal rejected for
    cycle/self-ref/unknown-id reasons).
    """

    candidates, organizations = _collect_organizations(assembled)
    warnings: list[str] = []
    if len(organizations) < 2:
        return assembled, warnings

    # Deep-copy so we don't mutate the caller's structures.
    new_root: dict[str, Any] | None = (
        deepcopy(assembled.root_entity)
        if isinstance(assembled.root_entity, dict)
        else None
    )
    new_related: list[Any] = [
        deepcopy(entity) if isinstance(entity, dict) else entity
        for entity in assembled.related_entities
    ]
    new_candidates: list[dict[str, Any]] = []
    if new_root is not None:
        new_candidates.append(new_root)
    new_candidates.extend(e for e in new_related if isinstance(e, dict))
    new_organizations = [
        entity for entity in new_candidates if entity.get("type") == ORGANIZATION_TYPE
    ]

    org_index: dict[str, dict[str, Any]] = {}
    for entity in new_organizations:
        entity_id = entity.get("id")
        if isinstance(entity_id, str) and entity_id:
            org_index.setdefault(entity_id, entity)
    if len(org_index) < 2:
        return assembled, warnings

    runner = agent or LLMOrgRelationshipsAgentV2()
    prompt_orgs = [
        _organization_summary_for_prompt(entity) for entity in new_organizations
    ]
    result = await runner.run(
        {
            "source_url": source_url,
            "organizations": prompt_orgs,
        },
        providers,
    )

    proposals = result.data.get("relationships") if isinstance(result.data, dict) else []
    if not isinstance(proposals, list):
        proposals = []

    # Existing parent edges (multi-parent DAG) for cycle detection.
    existing_edges: dict[str, list[str]] = {}
    for entity in new_organizations:
        child_id = entity.get("id")
        if not isinstance(child_id, str) or not child_id:
            continue
        raw = entity.get(UNIT_OF_KEY) or []
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, list):
            for parent_ref in raw:
                if isinstance(parent_ref, dict):
                    parent_ref = parent_ref.get("@id")
                if isinstance(parent_ref, str) and parent_ref:
                    existing_edges.setdefault(child_id, []).append(parent_ref)

    seen_children: set[str] = set()
    applied_count = 0
    rejected_count = 0
    already_present_count = 0

    for proposal in proposals:
        child_id = proposal.get("child_id") if isinstance(proposal, dict) else None
        parent_id = proposal.get("parent_id") if isinstance(proposal, dict) else None
        reason = proposal.get("reason") if isinstance(proposal, dict) else None

        if not isinstance(child_id, str) or not isinstance(parent_id, str):
            continue
        if child_id == parent_id:
            rejected_count += 1
            warnings.append(
                f"org_relationships: rejected self-reference '{child_id}'.",
            )
            continue
        if child_id in seen_children:
            rejected_count += 1
            warnings.append(
                f"org_relationships: rejected duplicate parent for '{child_id}'.",
            )
            continue
        if child_id not in org_index or parent_id not in org_index:
            rejected_count += 1
            missing = (
                child_id if child_id not in org_index else parent_id
            )
            warnings.append(
                f"org_relationships: rejected pair (child={child_id}, "
                f"parent={parent_id}) — '{missing}' is not in the graph.",
            )
            continue
        if _would_create_cycle(child_id, parent_id, existing_edges):
            rejected_count += 1
            warnings.append(
                f"org_relationships: rejected pair (child={child_id}, "
                f"parent={parent_id}) — would create a cycle.",
            )
            continue

        child_entity = org_index[child_id]
        parent_entity = org_index[parent_id]
        unit_of_set = _set_unit_of(child_entity, parent_id)
        has_unit_set = _add_to_has_unit(parent_entity, child_id)
        existing_edges.setdefault(child_id, []).append(parent_id)
        seen_children.add(child_id)

        if unit_of_set or has_unit_set:
            applied_count += 1
        else:
            already_present_count += 1

        if unit_of_set:
            warnings.append(
                f"Inferred org:unitOf on {child_id} → {parent_id}"
                + (f" ({reason})" if isinstance(reason, str) and reason else "")
                + ".",
            )
        if has_unit_set:
            warnings.append(
                f"Inferred org:hasUnit on {parent_id} → {child_id}.",
            )

    # Always emit a summary line so the stage's run is visible even when
    # every proposed edge was already in place (silent success).
    summary = (
        f"org_relationships: orgs={len(org_index)} proposed={len(proposals)} "
        f"applied={applied_count} already_present={already_present_count} "
        f"rejected={rejected_count}"
    )
    warnings.insert(0, summary)

    updated = AssembledOutput(
        root_entity=new_root if new_root is not None else assembled.root_entity,
        related_entities=new_related,
        excluded_entities=list(assembled.excluded_entities),
        warnings=list(assembled.warnings),
    )
    return updated, warnings


__all__ = ["run_org_relationships_stage"]
