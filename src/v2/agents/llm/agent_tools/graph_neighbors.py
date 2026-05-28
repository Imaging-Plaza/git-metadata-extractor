from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Tool

logger = logging.getLogger(__name__)

NEIGHBOR_LIMIT = 25


def _entity_type(entity: dict[str, Any]) -> str | None:
    raw = entity.get("type") or entity.get("@type")
    if isinstance(raw, list) and raw:
        return str(raw[0])
    if isinstance(raw, str):
        return raw
    return None


def _summarize(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entity.get("id") or entity.get("@id"),
        "name": entity.get("schema:name"),
        "type": _entity_type(entity),
    }


def _build_neighbor_index(graph: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict]]:
    """Group entities by their @id for O(1) lookup."""

    index: dict[str, list[dict]] = {}
    for bucket in graph.values():
        for entity in bucket:
            entity_id = entity.get("id") or entity.get("@id")
            if isinstance(entity_id, str) and entity_id:
                index.setdefault(entity_id, []).append(entity)
    return index


def _resolve_id_ref(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("@id") or value.get("id")
    if isinstance(value, str):
        return value
    return None


def _neighbors_for_organization(
    *,
    target_id: str,
    entities: dict[str, list[dict[str, Any]]],
    memberships: list[dict[str, Any]],
    index: dict[str, list[dict]],
) -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    for membership in memberships:
        if _resolve_id_ref(membership.get("org:organization")) != target_id:
            continue
        membership_id = membership.get("id") or membership.get("@id") or ""
        # Membership @id is composite: "{personId}_{orgId}"
        person_id = (
            membership_id[: -(len(target_id) + 1)]
            if membership_id.endswith(f"_{target_id}")
            else None
        )
        person_name = None
        if person_id and (entries := index.get(person_id)):
            person_name = entries[0].get("schema:name")
        members.append(
            {"id": person_id, "name": person_name, "role": membership.get("org:role")},
        )
        if len(members) >= NEIGHBOR_LIMIT:
            break

    child_orgs: list[dict[str, Any]] = []
    parent_orgs: list[dict[str, Any]] = []
    for organization in entities.get("organizations", []):
        unit_of = organization.get("org:unitOf") or []
        if not isinstance(unit_of, list):
            unit_of = [unit_of] if isinstance(unit_of, str) else []
        if organization.get("id") == target_id:
            for parent_id in unit_of:
                if isinstance(parent_id, str) and parent_id and (entries := index.get(parent_id)):
                    parent_orgs.append(_summarize(entries[0]))
            continue
        if target_id in unit_of:
            child_orgs.append(_summarize(organization))
            if len(child_orgs) >= NEIGHBOR_LIMIT:
                break

    owned_repos: list[dict[str, Any]] = []
    for organization in entities.get("organizations", []):
        if organization.get("id") != target_id:
            continue
        for repo_id in organization.get("pulse:owns") or []:
            if not isinstance(repo_id, str):
                continue
            entries = index.get(repo_id) or []
            owned_repos.append(
                _summarize(entries[0]) if entries else {"id": repo_id, "name": None, "type": None},
            )
            if len(owned_repos) >= NEIGHBOR_LIMIT:
                break

    return {
        "members": members,
        "child_orgs": child_orgs,
        "parent_orgs": parent_orgs,
        "owned_repos": owned_repos,
    }


def _neighbors_for_person(
    *,
    target_id: str,
    entities: dict[str, list[dict[str, Any]]],
    memberships: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
    index: dict[str, list[dict]],
) -> dict[str, Any]:
    affiliations: list[dict[str, Any]] = []
    for membership in memberships:
        membership_id = membership.get("id") or membership.get("@id") or ""
        if not membership_id.startswith(f"{target_id}_"):
            continue
        org_id = _resolve_id_ref(membership.get("org:organization"))
        org_name = None
        if org_id and (entries := index.get(org_id)):
            org_name = entries[0].get("schema:name")
        affiliations.append(
            {
                "org_id": org_id,
                "org_name": org_name,
                "role": membership.get("org:role"),
                "begin": membership.get("time:hasBeginning"),
                "end": membership.get("time:hasEnd"),
            },
        )
        if len(affiliations) >= NEIGHBOR_LIMIT:
            break

    contributed_repos: list[dict[str, Any]] = []
    for contribution in contributions:
        if _resolve_id_ref(contribution.get("schema:author")) != target_id:
            continue
        repo_id = _resolve_id_ref(contribution.get("pulse:contributionTo"))
        repo_name = None
        if repo_id and (entries := index.get(repo_id)):
            repo_name = entries[0].get("schema:name")
        contributed_repos.append(
            {
                "repo_id": repo_id,
                "repo_name": repo_name,
                "count": contribution.get("pulse:contributionCount"),
            },
        )
        if len(contributed_repos) >= NEIGHBOR_LIMIT:
            break

    owned_repos: list[dict[str, Any]] = []
    for person in entities.get("persons", []):
        if person.get("id") != target_id:
            continue
        for repo_id in person.get("pulse:owns") or []:
            if not isinstance(repo_id, str):
                continue
            entries = index.get(repo_id) or []
            owned_repos.append(
                _summarize(entries[0]) if entries else {"id": repo_id, "name": None, "type": None},
            )
            if len(owned_repos) >= NEIGHBOR_LIMIT:
                break

    return {"affiliations": affiliations, "contributed_repos": contributed_repos, "owned_repos": owned_repos}


def _neighbors_for_repository(
    *,
    target_id: str,
    entities: dict[str, list[dict[str, Any]]],
    contributions: list[dict[str, Any]],
    index: dict[str, list[dict]],
) -> dict[str, Any]:
    contributors: list[dict[str, Any]] = []
    for contribution in contributions:
        if _resolve_id_ref(contribution.get("pulse:contributionTo")) != target_id:
            continue
        person_id = _resolve_id_ref(contribution.get("schema:author"))
        person_name = None
        if person_id and (entries := index.get(person_id)):
            person_name = entries[0].get("schema:name")
        contributors.append(
            {
                "person_id": person_id,
                "person_name": person_name,
                "count": contribution.get("pulse:contributionCount"),
            },
        )
        if len(contributors) >= NEIGHBOR_LIMIT:
            break

    owners: list[dict[str, Any]] = []
    for bucket_key in ("organizations", "persons"):
        for entity in entities.get(bucket_key, []):
            for owned_id in entity.get("pulse:owns") or []:
                if owned_id == target_id:
                    owners.append(_summarize(entity))
                    break
            if len(owners) >= NEIGHBOR_LIMIT:
                break

    target = (index.get(target_id) or [{}])[0]
    fork_of = target.get("pulse:isForkOf")
    if isinstance(fork_of, dict):
        fork_of = fork_of.get("@id") or fork_of.get("id")
    fork_of_summary: dict[str, Any] | None = None
    if isinstance(fork_of, str) and (entries := index.get(fork_of)):
        fork_of_summary = _summarize(entries[0])

    return {"contributors": contributors, "owners": owners, "fork_of": fork_of_summary}


def make_get_entity_neighbors_tool(
    *,
    entities: dict[str, list[dict[str, Any]]],
    memberships: list[dict[str, Any]],
    contributions: list[dict[str, Any]] | None = None,
) -> Tool:
    """Build a Pydantic-AI tool exposing graph neighbors of any entity under refinement.

    Supports `org:Organization`, `schema:Person`, and `schema:SoftwareSourceCode`
    (Repository) targets. Returns small `{id, name, type}` summaries — no full
    payloads. Closure captures the reconciled `entities`, `memberships`, and
    `contributions` so the agent can inspect structural neighbors without I/O.
    """

    index = _build_neighbor_index(entities)
    contributions_list = list(contributions or [])

    def get_entity_neighbors(entity_id: str) -> dict[str, Any]:
        """Return a small JSON dict summarising neighbors of `entity_id` in the current graph."""

        logger.info("tool call: get_entity_neighbors entity_id=%s", entity_id)
        target_list = index.get(entity_id, [])
        if not target_list:
            return {"error": f"entity_id not found in graph: {entity_id}"}

        target = target_list[0]
        target_type = _entity_type(target)
        result: dict[str, Any] = {"entity": _summarize(target), "type": target_type}

        if target_type == "org:Organization":
            result.update(
                _neighbors_for_organization(
                    target_id=entity_id,
                    entities=entities,
                    memberships=memberships,
                    index=index,
                ),
            )
        elif target_type == "schema:Person":
            result.update(
                _neighbors_for_person(
                    target_id=entity_id,
                    entities=entities,
                    memberships=memberships,
                    contributions=contributions_list,
                    index=index,
                ),
            )
        elif target_type == "schema:SoftwareSourceCode":
            result.update(
                _neighbors_for_repository(
                    target_id=entity_id,
                    entities=entities,
                    contributions=contributions_list,
                    index=index,
                ),
            )

        return result

    return Tool(
        get_entity_neighbors,
        name="get_entity_neighbors",
        description=(
            "Return a summary of neighbors for an entity in the current pipeline graph. "
            "Supports organizations (members, child/parent orgs, owned repos), persons "
            "(affiliations, contributed repos, owned repos), and repositories "
            "(contributors with commit counts, owners, fork lineage). Each neighbor is "
            "summarised as {id, name, type}. Use to understand structural context "
            "before proposing field improvements."
        ),
    )
