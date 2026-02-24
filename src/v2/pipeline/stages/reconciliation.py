from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from src.v2.canonicalization import (
    resolve_article_id,
    resolve_organization_id,
    resolve_person_id,
    resolve_repository_id,
)
from src.v2.pipeline.stages.models import ReconciledEntities


def _as_entity_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduplicated.append(value)
        seen.add(value)
    return deduplicated


def _normalize_lookup_token(token: str) -> str:
    return token.strip().lower()


def _register_lookup_token(lookup: dict[str, str], token: Any, canonical_id: str) -> None:
    if not isinstance(token, str):
        return
    normalized = _normalize_lookup_token(token)
    if not normalized:
        return
    lookup[normalized] = canonical_id


def _resolve_lookup_token(lookup: dict[str, str], token: Any) -> str | None:
    if not isinstance(token, str):
        return None
    return lookup.get(_normalize_lookup_token(token))


def _register_person_lookup_tokens(lookup: dict[str, str], person: dict[str, Any]) -> None:
    canonical_id = person["id"]
    _register_lookup_token(lookup, canonical_id, canonical_id)
    _register_lookup_token(lookup, person.get("pulse:githubUsername"), canonical_id)
    _register_lookup_token(lookup, person.get("schema:name"), canonical_id)

    identifiers = person.get("identifiers")
    if isinstance(identifiers, dict):
        _register_lookup_token(lookup, identifiers.get("pulse:githubUsername"), canonical_id)
        _register_lookup_token(lookup, identifiers.get("uuid"), canonical_id)


def _register_organization_lookup_tokens(
    lookup: dict[str, str],
    organization: dict[str, Any],
) -> None:
    canonical_id = organization["id"]
    _register_lookup_token(lookup, canonical_id, canonical_id)
    _register_lookup_token(lookup, organization.get("schema:name"), canonical_id)
    _register_lookup_token(lookup, organization.get("pulse:githubOrganizationHandle"), canonical_id)
    _register_lookup_token(lookup, organization.get("pulse:ror"), canonical_id)
    _register_lookup_token(lookup, organization.get("schema:identifier"), canonical_id)

    identifiers = organization.get("identifiers")
    if isinstance(identifiers, dict):
        _register_lookup_token(lookup, identifiers.get("pulse:githubOrganizationHandle"), canonical_id)
        _register_lookup_token(lookup, identifiers.get("pulse:ror"), canonical_id)
        _register_lookup_token(
            lookup,
            identifiers.get("pulse:infoscienceOrganizationIdentifier"),
            canonical_id,
        )


def _register_repository_lookup_tokens(
    lookup: dict[str, str],
    repository: dict[str, Any],
) -> None:
    canonical_id = repository["id"]
    _register_lookup_token(lookup, canonical_id, canonical_id)
    _register_lookup_token(lookup, repository.get("pulse:githubRepositoryHandle"), canonical_id)
    _register_lookup_token(lookup, repository.get("schema:identifier"), canonical_id)

    identifiers = repository.get("identifiers")
    if isinstance(identifiers, dict):
        _register_lookup_token(lookup, identifiers.get("pulse:githubRepositoryHandle"), canonical_id)
        _register_lookup_token(lookup, identifiers.get("schema:identifier"), canonical_id)


def _build_membership(person_id: str, org_id: str) -> dict[str, Any]:
    membership_id = f"{person_id}_{org_id}"
    return {
        "id": membership_id,
        "type": "org:Membership",
        "shacl": "pulse:MembershipShape",
        "identifiers": {
            "pulse:composite": membership_id,
            "uuid": str(uuid4()),
        },
        "idSource": "pulse:composite",
        "org:organization": org_id,
        "org:role": None,
        "time:hasBeginning": None,
        "time:hasEnd": None,
    }


def _build_contribution(person_id: str, repository_id: str) -> dict[str, Any]:
    contribution_id = f"{person_id}_{repository_id}"
    return {
        "id": contribution_id,
        "type": "pulse:Contribution",
        "shacl": "pulse:ContributionShape",
        "identifiers": {
            "pulse:composite": contribution_id,
            "uuid": str(uuid4()),
        },
        "idSource": "pulse:composite",
        "pulse:contributionTo": repository_id,
        "pulse:contributionCount": 0,
        "pulse:firstContributionDate": None,
        "pulse:lastContributionDate": None,
        "schema:author": person_id,
    }


def _extract_affiliation_reference(affiliation: Any) -> str | None:
    if isinstance(affiliation, str):
        return affiliation
    if isinstance(affiliation, dict):
        for key in ("organizationId", "name", "schema:name"):
            value = affiliation.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _detect_repository_fork_cycles(repositories: list[dict[str, Any]]) -> list[str]:
    repository_ids = {
        repository["id"]
        for repository in repositories
        if isinstance(repository.get("id"), str)
    }
    graph = {
        repository["id"]: repository.get("pulse:isForkOf")
        for repository in repositories
        if isinstance(repository.get("id"), str)
        and isinstance(repository.get("pulse:isForkOf"), str)
        and repository.get("pulse:isForkOf") in repository_ids
    }

    warnings: list[str] = []
    visit_state: dict[str, int] = {}
    stack: list[str] = []

    def _visit(node_id: str) -> None:
        visit_state[node_id] = 1
        stack.append(node_id)

        target = graph.get(node_id)
        if isinstance(target, str):
            target_state = visit_state.get(target, 0)
            if target_state == 0:
                _visit(target)
            elif target_state == 1:
                cycle_start_index = stack.index(target)
                cycle_path = " -> ".join([*stack[cycle_start_index:], target])
                warnings.append(f"Circular repository fork reference detected: {cycle_path}")

        stack.pop()
        visit_state[node_id] = 2

    for node in graph:
        if visit_state.get(node, 0) == 0:
            _visit(node)

    return _dedupe_preserve_order(warnings)


def reconcile_entities(  # noqa: C901, PLR0912, PLR0915
    entities_by_type: dict[str, Any],
) -> ReconciledEntities:
    reconciled_entities: dict[str, list[dict[str, Any]]] = {
        key: deepcopy(_as_entity_list(value))
        for key, value in entities_by_type.items()
        if isinstance(value, list)
    }

    persons = reconciled_entities.setdefault("persons", [])
    organizations = reconciled_entities.setdefault("organizations", [])
    repositories = reconciled_entities.setdefault("repositories", [])
    articles = reconciled_entities.setdefault("articles", [])

    person_lookup: dict[str, str] = {}
    organization_lookup: dict[str, str] = {}
    repository_lookup: dict[str, str] = {}

    for person in persons:
        canonical_id, id_source = resolve_person_id(person)
        person["id"] = canonical_id
        person["idSource"] = id_source
        _register_person_lookup_tokens(person_lookup, person)

    for organization in organizations:
        canonical_id, id_source = resolve_organization_id(organization)
        organization["id"] = canonical_id
        organization["idSource"] = id_source
        _register_organization_lookup_tokens(organization_lookup, organization)

    for repository in repositories:
        canonical_id, id_source = resolve_repository_id(repository)
        repository["id"] = canonical_id
        repository["idSource"] = id_source
        _register_repository_lookup_tokens(repository_lookup, repository)

    for article in articles:
        canonical_id, id_source = resolve_article_id(article)
        article["id"] = canonical_id
        article["idSource"] = id_source

    membership_pairs: set[tuple[str, str]] = set()
    contribution_pairs: set[tuple[str, str]] = set()
    link_warnings: list[str] = []

    for repository in repositories:
        repository_id = repository["id"]
        author_refs_value = repository.get("schema:author")
        author_refs: list[Any]
        if isinstance(author_refs_value, list):
            author_refs = author_refs_value
        else:
            fallback_authors = repository.get("authors")
            author_refs = fallback_authors if isinstance(fallback_authors, list) else []

        canonical_authors: list[str] = []
        for author_ref in author_refs:
            canonical_author = _resolve_lookup_token(person_lookup, author_ref)
            if canonical_author is None:
                link_warnings.append(
                    (
                        "Orphan person reference from repository author list: "
                        f"repo={repository_id}, author={author_ref}"
                    ),
                )
                continue
            canonical_authors.append(canonical_author)
            contribution_pairs.add((canonical_author, repository_id))

        canonical_authors = _dedupe_preserve_order(canonical_authors)
        repository["schema:author"] = canonical_authors
        if "authors" in repository:
            repository["authors"] = list(canonical_authors)

        owned_by = repository.get("pulse:ownedBy")
        if isinstance(owned_by, str):
            canonical_owner = _resolve_lookup_token(person_lookup, owned_by) or _resolve_lookup_token(
                organization_lookup,
                owned_by,
            )
            if canonical_owner is None:
                link_warnings.append(
                    (
                        "Orphan owner reference from repository: "
                        f"repo={repository_id}, owner={owned_by}"
                    ),
                )
            else:
                repository["pulse:ownedBy"] = canonical_owner

        fork_of = repository.get("pulse:isForkOf")
        if isinstance(fork_of, str):
            canonical_fork_of = _resolve_lookup_token(repository_lookup, fork_of)
            if canonical_fork_of is None:
                link_warnings.append(
                    (
                        "Orphan repository fork reference: "
                        f"repo={repository_id}, fork={fork_of}"
                    ),
                )
            else:
                repository["pulse:isForkOf"] = canonical_fork_of

    for article in articles:
        article_id = article["id"]
        author_refs_value = article.get("schema:author")
        if isinstance(author_refs_value, list):
            canonical_authors: list[str] = []
            for author_ref in author_refs_value:
                canonical_author = _resolve_lookup_token(person_lookup, author_ref)
                if canonical_author is None:
                    link_warnings.append(
                        (
                            "Orphan person reference from article author list: "
                            f"article={article_id}, author={author_ref}"
                        ),
                    )
                    continue
                canonical_authors.append(canonical_author)
            article["schema:author"] = _dedupe_preserve_order(canonical_authors)

        source_org_ref = article.get("schema:sourceOrganization")
        if isinstance(source_org_ref, str):
            canonical_source_org = _resolve_lookup_token(organization_lookup, source_org_ref)
            if canonical_source_org is None:
                link_warnings.append(
                    (
                        "Orphan organization reference from article source organization: "
                        f"article={article_id}, organization={source_org_ref}"
                    ),
                )
            else:
                article["schema:sourceOrganization"] = canonical_source_org

    for person in persons:
        person_id = person["id"]
        affiliations = person.get("affiliations")
        canonical_affiliations: list[str] = []
        if isinstance(affiliations, list):
            for affiliation in affiliations:
                affiliation_ref = _extract_affiliation_reference(affiliation)
                canonical_affiliation = _resolve_lookup_token(organization_lookup, affiliation_ref)
                if canonical_affiliation is None:
                    if isinstance(affiliation_ref, str):
                        link_warnings.append(
                            (
                                "Orphan organization reference from person affiliation: "
                                f"person={person_id}, affiliation={affiliation_ref}"
                            ),
                        )
                    continue
                canonical_affiliations.append(canonical_affiliation)
                membership_pairs.add((person_id, canonical_affiliation))
            person["affiliations"] = _dedupe_preserve_order(canonical_affiliations)

        owns_refs = person.get("pulse:owns")
        if isinstance(owns_refs, list):
            canonical_owns: list[str] = []
            for owned_repo in owns_refs:
                canonical_repo_id = _resolve_lookup_token(repository_lookup, owned_repo)
                if canonical_repo_id is None:
                    if isinstance(owned_repo, str):
                        link_warnings.append(
                            (
                                "Orphan repository ownership reference from person: "
                                f"person={person_id}, repository={owned_repo}"
                            ),
                        )
                    continue
                canonical_owns.append(canonical_repo_id)
            person["pulse:owns"] = _dedupe_preserve_order(canonical_owns)

    for organization in organizations:
        organization_id = organization["id"]
        owns_refs = organization.get("pulse:owns")
        if isinstance(owns_refs, list):
            canonical_org_owns: list[str] = []
            for owned_repo in owns_refs:
                canonical_repo_id = _resolve_lookup_token(repository_lookup, owned_repo)
                if canonical_repo_id is None:
                    if isinstance(owned_repo, str):
                        link_warnings.append(
                            (
                                "Orphan repository ownership reference from organization: "
                                f"organization={organization_id}, repository={owned_repo}"
                            ),
                        )
                    continue
                canonical_org_owns.append(canonical_repo_id)
            organization["pulse:owns"] = _dedupe_preserve_order(canonical_org_owns)

    memberships = [
        _build_membership(person_id, org_id)
        for person_id, org_id in sorted(membership_pairs)
    ]
    contributions = [
        _build_contribution(person_id, repository_id)
        for person_id, repository_id in sorted(contribution_pairs)
    ]

    membership_ids_by_person: dict[str, list[str]] = {}
    for membership in memberships:
        membership_id = membership["id"]
        person_id, _ = membership_id.split("_", maxsplit=1)
        membership_ids_by_person.setdefault(person_id, []).append(membership_id)

    contribution_ids_by_person: dict[str, list[str]] = {}
    for contribution in contributions:
        contribution_id = contribution["id"]
        person_id, _ = contribution_id.split("_", maxsplit=1)
        contribution_ids_by_person.setdefault(person_id, []).append(contribution_id)

    for person in persons:
        person_id = person["id"]
        person["org:hasMembership"] = _dedupe_preserve_order(
            membership_ids_by_person.get(person_id, []),
        )
        person["pulse:hasContribution"] = _dedupe_preserve_order(
            contribution_ids_by_person.get(person_id, []),
        )

    link_warnings.extend(_detect_repository_fork_cycles(repositories))

    return ReconciledEntities(
        entities=reconciled_entities,
        memberships=memberships,
        contributions=contributions,
        link_warnings=_dedupe_preserve_order(link_warnings),
    )
