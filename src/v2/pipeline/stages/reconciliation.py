from __future__ import annotations

import re
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
from src.v2.pipeline.stages.privacy import anonymize_email

UUID_V4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    flags=re.IGNORECASE,
)
INFOSCIENCE_UUID_PATTERN = re.compile(
    r"(?:entities/(?:person|organization|publication)|core/items)/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})",
    flags=re.IGNORECASE,
)
ORCID_PATTERN = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[0-9X]$", flags=re.IGNORECASE)


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


def _normalize_uuid_v4(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    if not candidate or not UUID_V4_PATTERN.match(candidate):
        return None
    return candidate


def _normalize_orcid_token(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.lower().startswith("https://orcid.org/"):
        candidate = candidate.rsplit("/", maxsplit=1)[-1]
    if not candidate or not ORCID_PATTERN.match(candidate):
        return None
    return candidate


def _normalize_infoscience_uuid(value: Any) -> str | None:
    direct_uuid = _normalize_uuid_v4(value)
    if direct_uuid is not None:
        return direct_uuid
    if not isinstance(value, str):
        return None
    match = INFOSCIENCE_UUID_PATTERN.search(value.strip())
    if match is None:
        return None
    return _normalize_uuid_v4(match.group(1))


def _normalize_person_identifiers(person: dict[str, Any]) -> None:
    identifiers = person.get("identifiers")
    if not isinstance(identifiers, dict):
        identifiers = {}

    normalized_orcid = _normalize_orcid_token(
        identifiers.get("pulse:orcid") or person.get("pulse:orcidIdentifier"),
    )
    normalized_infoscience_id = _normalize_infoscience_uuid(
        identifiers.get("pulse:infosciencePersonIdentifier")
        or person.get("pulse:infosciencePersonIdentifier"),
    )
    github_username = identifiers.get("pulse:githubUsername")
    if not isinstance(github_username, str) or not github_username:
        github_username = person.get("pulse:githubUsername")
    if not isinstance(github_username, str) or not github_username:
        github_username = None

    uuid_value = _normalize_uuid_v4(identifiers.get("uuid"))
    if uuid_value is None:
        uuid_value = str(uuid4())

    person["identifiers"] = {
        "pulse:orcid": normalized_orcid,
        "pulse:infosciencePersonIdentifier": normalized_infoscience_id,
        "pulse:githubUsername": github_username,
        "uuid": uuid_value,
    }
    person["pulse:orcidIdentifier"] = normalized_orcid
    person["pulse:infosciencePersonIdentifier"] = normalized_infoscience_id
    person["pulse:githubUsername"] = github_username


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


def _extract_composite_pair(composite_id: Any) -> tuple[str | None, str | None]:
    if not isinstance(composite_id, str) or "_" not in composite_id:
        return None, None
    left, right = composite_id.split("_", maxsplit=1)
    if not left or not right:
        return None, None
    return left, right


def _normalize_membership_entities(
    memberships: list[dict[str, Any]],
    *,
    person_lookup: dict[str, str],
    organization_lookup: dict[str, str],
) -> tuple[list[dict[str, Any]], set[tuple[str, str]], list[str]]:
    normalized_memberships: list[dict[str, Any]] = []
    covered_pairs: set[tuple[str, str]] = set()
    warnings: list[str] = []
    seen_membership_ids: set[str] = set()

    for membership in memberships:
        composite_id_ref: Any = membership.get("id")
        identifiers = membership.get("identifiers")
        if isinstance(identifiers, dict):
            composite_id_ref = identifiers.get("pulse:composite") or composite_id_ref

        person_ref, org_ref = _extract_composite_pair(composite_id_ref)
        organization_ref = membership.get("org:organization")
        if isinstance(organization_ref, str):
            org_ref = organization_ref

        canonical_person_id = _resolve_lookup_token(person_lookup, person_ref)
        canonical_org_id = _resolve_lookup_token(organization_lookup, org_ref)
        if canonical_person_id is None or canonical_org_id is None:
            warnings.append(
                (
                    "Unresolved class membership reference during reconciliation: "
                    f"id={membership.get('id')}, person={person_ref}, organization={org_ref}"
                ),
            )
            continue

        canonical_membership_id = f"{canonical_person_id}_{canonical_org_id}"
        if canonical_membership_id in seen_membership_ids:
            continue
        seen_membership_ids.add(canonical_membership_id)
        covered_pairs.add((canonical_person_id, canonical_org_id))

        normalized_membership = deepcopy(membership)
        normalized_identifiers = (
            deepcopy(identifiers)
            if isinstance(identifiers, dict)
            else {}
        )
        normalized_identifiers["pulse:composite"] = canonical_membership_id
        uuid_value = normalized_identifiers.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value:
            normalized_identifiers["uuid"] = str(uuid4())

        normalized_membership["id"] = canonical_membership_id
        normalized_membership["type"] = "org:Membership"
        normalized_membership["shacl"] = "pulse:MembershipShape"
        normalized_membership["identifiers"] = normalized_identifiers
        normalized_membership["idSource"] = "pulse:composite"
        normalized_membership["org:organization"] = canonical_org_id
        if not isinstance(normalized_membership.get("org:role"), str):
            normalized_membership["org:role"] = None
        if not isinstance(normalized_membership.get("time:hasBeginning"), str):
            normalized_membership["time:hasBeginning"] = None
        if not isinstance(normalized_membership.get("time:hasEnd"), str):
            normalized_membership["time:hasEnd"] = None

        normalized_memberships.append(normalized_membership)

    return normalized_memberships, covered_pairs, warnings


def _normalize_contribution_entities(  # noqa: C901
    contributions: list[dict[str, Any]],
    *,
    person_lookup: dict[str, str],
    repository_lookup: dict[str, str],
) -> tuple[list[dict[str, Any]], set[tuple[str, str]], list[str]]:
    normalized_contributions: list[dict[str, Any]] = []
    covered_pairs: set[tuple[str, str]] = set()
    warnings: list[str] = []
    seen_contribution_ids: set[str] = set()

    for contribution in contributions:
        composite_id_ref: Any = contribution.get("id")
        identifiers = contribution.get("identifiers")
        if isinstance(identifiers, dict):
            composite_id_ref = identifiers.get("pulse:composite") or composite_id_ref

        person_ref, repository_ref = _extract_composite_pair(composite_id_ref)
        schema_author = contribution.get("schema:author")
        if isinstance(schema_author, str):
            person_ref = schema_author
        contribution_to = contribution.get("pulse:contributionTo")
        if isinstance(contribution_to, str):
            repository_ref = contribution_to

        canonical_person_id = _resolve_lookup_token(person_lookup, person_ref)
        canonical_repository_id = _resolve_lookup_token(repository_lookup, repository_ref)
        if canonical_person_id is None or canonical_repository_id is None:
            warnings.append(
                (
                    "Unresolved class contribution reference during reconciliation: "
                    f"id={contribution.get('id')}, person={person_ref}, "
                    f"repository={repository_ref}"
                ),
            )
            continue

        canonical_contribution_id = f"{canonical_person_id}_{canonical_repository_id}"
        if canonical_contribution_id in seen_contribution_ids:
            continue
        seen_contribution_ids.add(canonical_contribution_id)
        covered_pairs.add((canonical_person_id, canonical_repository_id))

        normalized_contribution = deepcopy(contribution)
        normalized_identifiers = (
            deepcopy(identifiers)
            if isinstance(identifiers, dict)
            else {}
        )
        normalized_identifiers["pulse:composite"] = canonical_contribution_id
        uuid_value = normalized_identifiers.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value:
            normalized_identifiers["uuid"] = str(uuid4())

        contribution_count = normalized_contribution.get("pulse:contributionCount")
        if not isinstance(contribution_count, int) or contribution_count < 0:
            contribution_count = 0

        normalized_contribution["id"] = canonical_contribution_id
        normalized_contribution["type"] = "pulse:Contribution"
        normalized_contribution["shacl"] = "pulse:ContributionShape"
        normalized_contribution["identifiers"] = normalized_identifiers
        normalized_contribution["idSource"] = "pulse:composite"
        normalized_contribution["schema:author"] = canonical_person_id
        normalized_contribution["pulse:contributionTo"] = canonical_repository_id
        normalized_contribution["pulse:contributionCount"] = contribution_count
        if not isinstance(normalized_contribution.get("pulse:firstContributionDate"), str):
            normalized_contribution["pulse:firstContributionDate"] = None
        if not isinstance(normalized_contribution.get("pulse:lastContributionDate"), str):
            normalized_contribution["pulse:lastContributionDate"] = None

        normalized_contributions.append(normalized_contribution)

    return normalized_contributions, covered_pairs, warnings


def reconcile_entities(  # noqa: C901, PLR0912, PLR0915
    entities_by_type: dict[str, Any],
) -> ReconciledEntities:
    reconciled_entities: dict[str, list[dict[str, Any]]] = {
        "persons": deepcopy(_as_entity_list(entities_by_type.get("persons"))),
        "organizations": deepcopy(_as_entity_list(entities_by_type.get("organizations"))),
        "repositories": deepcopy(_as_entity_list(entities_by_type.get("repositories"))),
        "articles": deepcopy(_as_entity_list(entities_by_type.get("articles"))),
    }
    class_memberships = deepcopy(_as_entity_list(entities_by_type.get("memberships")))
    class_contributions = deepcopy(_as_entity_list(entities_by_type.get("contributions")))

    persons = reconciled_entities["persons"]
    organizations = reconciled_entities["organizations"]
    repositories = reconciled_entities["repositories"]
    articles = reconciled_entities["articles"]

    person_lookup: dict[str, str] = {}
    organization_lookup: dict[str, str] = {}
    repository_lookup: dict[str, str] = {}

    for person in persons:
        canonical_id, id_source = resolve_person_id(person)
        person["id"] = canonical_id
        person["idSource"] = id_source
        _normalize_person_identifiers(person)
        _register_person_lookup_tokens(person_lookup, person)
        email = person.get("schema:email")
        if isinstance(email, str):
            person["schema:email"] = anonymize_email(email)

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

    fallback_membership_pairs: set[tuple[str, str]] = set()
    fallback_contribution_pairs: set[tuple[str, str]] = set()
    link_warnings: list[str] = []
    synthesis_warnings: list[str] = []

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
        unresolved_author_refs: list[str] = []
        for author_ref in author_refs:
            canonical_author = _resolve_lookup_token(person_lookup, author_ref)
            if canonical_author is None:
                link_warnings.append(
                    (
                        "Orphan person reference from repository author list: "
                        f"repo={repository_id}, author={author_ref}"
                    ),
                )
                if isinstance(author_ref, str) and author_ref:
                    unresolved_author_refs.append(author_ref)
                continue
            canonical_authors.append(canonical_author)
            fallback_contribution_pairs.add((canonical_author, repository_id))

        canonical_authors = _dedupe_preserve_order(
            [*canonical_authors, *unresolved_author_refs],
        )
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
            canonical_article_authors: list[str] = []
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
                canonical_article_authors.append(canonical_author)
            article["schema:author"] = _dedupe_preserve_order(canonical_article_authors)

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
                fallback_membership_pairs.add((person_id, canonical_affiliation))
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

    memberships, covered_membership_pairs, class_membership_warnings = _normalize_membership_entities(
        class_memberships,
        person_lookup=person_lookup,
        organization_lookup=organization_lookup,
    )
    link_warnings.extend(class_membership_warnings)
    for person_id, org_id in sorted(fallback_membership_pairs):
        if (person_id, org_id) in covered_membership_pairs:
            continue
        memberships.append(_build_membership(person_id, org_id))
        covered_membership_pairs.add((person_id, org_id))
        synthesis_warnings.append(
            (
                "Synthesized fallback membership entity due to missing class-agent link: "
                f"person={person_id}, organization={org_id}"
            ),
        )

    contributions, covered_contribution_pairs, class_contribution_warnings = _normalize_contribution_entities(
        class_contributions,
        person_lookup=person_lookup,
        repository_lookup=repository_lookup,
    )
    link_warnings.extend(class_contribution_warnings)
    for person_id, repository_id in sorted(fallback_contribution_pairs):
        if (person_id, repository_id) in covered_contribution_pairs:
            continue
        contributions.append(_build_contribution(person_id, repository_id))
        covered_contribution_pairs.add((person_id, repository_id))
        synthesis_warnings.append(
            (
                "Synthesized fallback contribution entity due to missing class-agent link: "
                f"person={person_id}, repository={repository_id}"
            ),
        )

    membership_ids_by_person: dict[str, list[str]] = {}
    for membership in memberships:
        membership_id = membership["id"]
        person_id, _ = _extract_composite_pair(membership_id)
        if person_id is None:
            continue
        membership_ids_by_person.setdefault(person_id, []).append(membership_id)

    contribution_ids_by_person: dict[str, list[str]] = {}
    for contribution in contributions:
        contribution_id = contribution["id"]
        person_id, _ = _extract_composite_pair(contribution_id)
        if person_id is None:
            continue
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
        synthesis_warnings=_dedupe_preserve_order(synthesis_warnings),
    )
