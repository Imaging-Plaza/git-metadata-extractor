from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote

import pytest
from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef

from git_metadata_extractor.validation import shacl_validation as shacl_validation_module
from git_metadata_extractor.validation.ontology import load_ontology_shapes_graph
from git_metadata_extractor.validation.shacl_validation import (
    SHACLRuntimeUnavailableError,
    SHACLValidationResult,
    SHACLValidator,
)

SCHEMA = Namespace("http://schema.org/")
PULSE = Namespace("https://open-pulse.epfl.ch/ontology#")
ORG = Namespace("http://www.w3.org/ns/org#")
TIME = Namespace("http://www.w3.org/2006/time#")
WD = Namespace("http://www.wikidata.org/entity/")
ENTITY_BASE = "https://open-pulse.epfl.ch/entity/"
HAS_PYSHACL = shacl_validation_module.pyshacl_validate is not None


def _entity_ref(value: str) -> URIRef:
    if value.startswith(("https://", "http://")):
        return URIRef(value)
    return URIRef(f"{ENTITY_BASE}{quote(value, safe='')}")


def _prefixed_to_iri(value: str) -> URIRef:
    if value.startswith("pulse:"):
        return PULSE[value.split(":", maxsplit=1)[1]]
    if value.startswith("wd:"):
        return WD[value.split(":", maxsplit=1)[1]]
    if value.startswith(("https://", "http://")):
        return URIRef(value)
    return _entity_ref(value)


def _as_text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _add_person(graph: Graph, person: dict[str, Any]) -> None:
    subject = _entity_ref(person["id"])
    graph.add((subject, RDF.type, SCHEMA.Person))

    for key, predicate in (
        ("schema:name", SCHEMA.name),
        ("schema:email", SCHEMA.email),
        ("pulse:githubUsername", PULSE.githubUsername),
        ("pulse:orcidIdentifier", PULSE.orcidIdentifier),
        ("pulse:infosciencePersonIdentifier", PULSE.infosciencePersonIdentifier),
    ):
        value = _as_text(person.get(key))
        if value is not None:
            graph.add((subject, predicate, Literal(value)))

    url_value = _as_text(person.get("schema:url"))
    if url_value is not None:
        graph.add((subject, SCHEMA.url, URIRef(url_value)))

    for membership in person.get("org:hasMembership", []):
        if isinstance(membership, str):
            graph.add((subject, ORG.hasMembership, _entity_ref(membership)))

    for contribution in person.get("pulse:hasContribution", []):
        if isinstance(contribution, str):
            graph.add((subject, PULSE.hasContribution, _entity_ref(contribution)))

    for owned_repo in person.get("pulse:owns", []):
        if isinstance(owned_repo, str):
            graph.add((subject, PULSE.owns, _entity_ref(owned_repo)))


def _add_organization(graph: Graph, organization: dict[str, Any]) -> None:  # noqa: C901
    subject = _entity_ref(organization["id"])
    graph.add((subject, RDF.type, ORG.Organization))

    name = _as_text(organization.get("schema:name"))
    if name is not None:
        graph.add((subject, SCHEMA.name, Literal(name)))

    ror = _as_text(organization.get("schema:identifier"))
    if ror is not None:
        graph.add((subject, SCHEMA.identifier, Literal(ror)))

    github_handle = _as_text(organization.get("pulse:githubOrganizationHandle"))
    if github_handle is not None:
        graph.add((subject, PULSE.githubOrganizationHandle, Literal(github_handle)))

    infoscience_identifier = _as_text(
        organization.get("pulse:infoscienceOrganizationIdentifier"),
    )
    if infoscience_identifier is not None:
        graph.add(
            (
                subject,
                PULSE.infoscienceOrganizationIdentifier,
                Literal(infoscience_identifier),
            ),
        )

    org_type = _as_text(organization.get("pulse:OrganizationType"))
    if org_type is not None:
        graph.add((subject, PULSE.OrganizationType, _prefixed_to_iri(org_type)))

    followers = _as_int(organization.get("pulse:githubOrgFollowers"))
    if followers is not None:
        graph.add((subject, PULSE.githubOrgFollowers, Literal(followers)))

    for unit in organization.get("org:hasUnit", []):
        if isinstance(unit, str):
            graph.add((subject, ORG.hasUnit, _entity_ref(unit)))

    unit_of = _as_text(organization.get("org:unitOf"))
    if unit_of is not None:
        graph.add((subject, ORG.unitOf, _entity_ref(unit_of)))

    for owned_repo in organization.get("pulse:owns", []):
        if isinstance(owned_repo, str):
            graph.add((subject, PULSE.owns, _entity_ref(owned_repo)))


def _add_repository(graph: Graph, repository: dict[str, Any]) -> None:  # noqa: C901, PLR0912
    subject = _entity_ref(repository["id"])
    graph.add((subject, RDF.type, SCHEMA.SoftwareSourceCode))

    for key, predicate in (
        ("schema:name", SCHEMA.name),
        ("pulse:githubRepositoryHandle", PULSE.githubRepositoryHandle),
    ):
        value = _as_text(repository.get(key))
        if value is not None:
            graph.add((subject, predicate, Literal(value)))

    repository_type = _as_text(repository.get("pulse:repositoryType"))
    if repository_type is not None:
        graph.add((subject, PULSE.repositoryType, _prefixed_to_iri(repository_type)))

    for discipline in repository.get("pulse:discipline", []):
        if isinstance(discipline, str):
            graph.add((subject, PULSE.discipline, _prefixed_to_iri(discipline)))

    for author in repository.get("schema:author", []):
        if isinstance(author, str):
            graph.add((subject, SCHEMA.author, _entity_ref(author)))

    stars = _as_int(repository.get("pulse:githubRepoStars"))
    if stars is not None:
        graph.add((subject, PULSE.githubRepoStars, Literal(stars)))

    forks = _as_int(repository.get("pulse:githubRepoForks"))
    if forks is not None:
        graph.add((subject, PULSE.githubRepoForks, Literal(forks)))

    created_at = _as_text(repository.get("schema:dateCreated"))
    if created_at is not None:
        graph.add((subject, SCHEMA.dateCreated, Literal(created_at, datatype=XSD.dateTime)))

    license_iri = _as_text(repository.get("schema:license"))
    if license_iri is not None:
        graph.add((subject, SCHEMA.license, URIRef(license_iri)))

    citation_iri = _as_text(repository.get("schema:citation"))
    if citation_iri is not None:
        graph.add((subject, SCHEMA.citation, URIRef(citation_iri)))

    for language in repository.get("schema:programmingLanguage", []):
        if isinstance(language, str):
            graph.add((subject, SCHEMA.programmingLanguage, Literal(language)))

    owned_by = _as_text(repository.get("pulse:ownedBy"))
    if owned_by is not None:
        graph.add((subject, PULSE.ownedBy, _entity_ref(owned_by)))

    is_fork_of = _as_text(repository.get("pulse:isForkOf"))
    if is_fork_of is not None:
        graph.add((subject, PULSE.isForkOf, _entity_ref(is_fork_of)))


def _add_membership(graph: Graph, membership: dict[str, Any]) -> None:
    subject = _entity_ref(membership["id"])
    graph.add((subject, RDF.type, ORG.Membership))

    organization = _as_text(membership.get("org:organization"))
    if organization is not None:
        graph.add((subject, ORG.organization, _entity_ref(organization)))

    role = _as_text(membership.get("org:role"))
    if role is not None:
        graph.add((subject, ORG.role, Literal(role)))

    started = _as_text(membership.get("time:hasBeginning"))
    if started is not None:
        graph.add((subject, TIME.hasBeginning, Literal(started, datatype=XSD.date)))

    ended = _as_text(membership.get("time:hasEnd"))
    if ended is not None:
        graph.add((subject, TIME.hasEnd, Literal(ended, datatype=XSD.date)))


def _add_contribution(graph: Graph, contribution: dict[str, Any]) -> None:
    subject = _entity_ref(contribution["id"])
    graph.add((subject, RDF.type, PULSE.Contribution))

    contribution_to = _as_text(contribution.get("pulse:contributionTo"))
    if contribution_to is not None:
        graph.add((subject, PULSE.contributionTo, _entity_ref(contribution_to)))

    contribution_count = _as_int(contribution.get("pulse:contributionCount"))
    if contribution_count is not None:
        graph.add((subject, PULSE.contributionCount, Literal(contribution_count)))

    first_contribution = _as_text(contribution.get("pulse:firstContributionDate"))
    if first_contribution is not None:
        graph.add(
            (
                subject,
                PULSE.firstContributionDate,
                Literal(first_contribution, datatype=XSD.dateTime),
            ),
        )

    last_contribution = _as_text(contribution.get("pulse:lastContributionDate"))
    if last_contribution is not None:
        graph.add(
            (
                subject,
                PULSE.lastContributionDate,
                Literal(last_contribution, datatype=XSD.dateTime),
            ),
        )

    author = _as_text(contribution.get("schema:author"))
    if author is not None:
        graph.add((subject, SCHEMA.author, _entity_ref(author)))


def _strict_fixtures_to_graph(load_fixture: Callable[[str, str], Any]) -> Graph:
    graph = Graph()
    for person in load_fixture("schema/strict", "pulse_PersonShape"):
        _add_person(graph, person)
    for organization in load_fixture("schema/strict", "pulse_OrganizationShape"):
        _add_organization(graph, organization)
    for repository in load_fixture("schema/strict", "pulse_RepositoryShape"):
        _add_repository(graph, repository)
    for membership in load_fixture("schema/strict", "pulse_MembershipShape"):
        _add_membership(graph, membership)
    for contribution in load_fixture("schema/strict", "pulse_ContributionShape"):
        _add_contribution(graph, contribution)
    return graph


@pytest.mark.skipif(not HAS_PYSHACL, reason="pyshacl is not installed")
def test_shacl_validator_accepts_valid_fixture_graph(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = SHACLValidator()
    data_graph = _strict_fixtures_to_graph(load_fixture)
    shapes_graph = load_ontology_shapes_graph()

    result = validator.validate_graph(data_graph, shapes_graph)

    assert isinstance(result, SHACLValidationResult)
    assert result.conforms is True
    assert result.violations == []


@pytest.mark.skipif(not HAS_PYSHACL, reason="pyshacl is not installed")
def test_shacl_validator_rejects_person_without_any_identifier() -> None:
    validator = SHACLValidator()
    shapes_graph = load_ontology_shapes_graph()
    graph = Graph()

    person = _entity_ref("person-without-identifiers")
    graph.add((person, RDF.type, SCHEMA.Person))
    graph.add((person, SCHEMA.name, Literal("No Identifier Person")))

    result = validator.validate_graph(graph, shapes_graph)

    assert result.conforms is False
    assert result.violations
    assert any(
        violation["message"]
        and any(
            token in violation["message"]
            for token in ("githubUsername", "email", "infosciencePersonIdentifier", "sh:or")
        )
        for violation in result.violations
    )


@pytest.mark.skipif(not HAS_PYSHACL, reason="pyshacl is not installed")
def test_shacl_validator_rejects_repository_with_invalid_repository_type() -> None:
    validator = SHACLValidator()
    shapes_graph = load_ontology_shapes_graph()
    graph = Graph()

    person = _entity_ref("valid-person")
    graph.add((person, RDF.type, SCHEMA.Person))
    graph.add((person, SCHEMA.name, Literal("Valid Author")))
    graph.add((person, PULSE.githubUsername, Literal("validauthor")))

    repository = _entity_ref("owner/invalid-repo")
    graph.add((repository, RDF.type, SCHEMA.SoftwareSourceCode))
    graph.add((repository, SCHEMA.name, Literal("Invalid Type Repo")))
    graph.add((repository, PULSE.githubRepositoryHandle, Literal("owner/invalid-repo")))
    graph.add((repository, SCHEMA.author, person))
    graph.add((repository, PULSE.repositoryType, PULSE.InvalidRepositoryType))

    result = validator.validate_graph(graph, shapes_graph)

    assert result.conforms is False
    assert result.violations
    assert any(
        violation["path"] and "repositoryType" in violation["path"]
        for violation in result.violations
    )
    assert all(violation["message"] for violation in result.violations)


def test_ontology_shapes_graph_is_cached() -> None:
    first = load_ontology_shapes_graph()
    second = load_ontology_shapes_graph()

    assert first is second


def test_shacl_validator_reports_missing_runtime_dependency(monkeypatch) -> None:
    validator = SHACLValidator()
    monkeypatch.setattr(shacl_validation_module, "pyshacl_validate", None)

    with pytest.raises(
        SHACLRuntimeUnavailableError,
        match="pyshacl is not installed",
    ):
        validator.validate_graph(Graph(), Graph())
