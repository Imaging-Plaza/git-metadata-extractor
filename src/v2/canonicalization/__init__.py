"""Canonical ID resolution for v2 entities."""

from src.v2.canonicalization.doi import doi_iri, parse_doi
from src.v2.canonicalization.github import (
    github_org_iri,
    github_repo_iri,
    github_user_iri,
    parse_github_org_iri,
    parse_github_repo_iri,
    parse_github_user_iri,
)
from src.v2.canonicalization.id_resolution import (
    resolve_article_id,
    resolve_organization_id,
    resolve_person_id,
    resolve_repository_id,
)
from src.v2.canonicalization.infoscience import (
    infoscience_article_iri,
    infoscience_org_iri,
    infoscience_person_iri,
    parse_infoscience_iri,
)
from src.v2.canonicalization.orcid import ORCID_BARE_RE, orcid_iri, parse_orcid
from src.v2.canonicalization.string_utils import normalize_string

__all__ = [
    "ORCID_BARE_RE",
    "doi_iri",
    "github_org_iri",
    "github_repo_iri",
    "github_user_iri",
    "infoscience_article_iri",
    "infoscience_org_iri",
    "infoscience_person_iri",
    "normalize_string",
    "orcid_iri",
    "parse_doi",
    "parse_github_org_iri",
    "parse_github_repo_iri",
    "parse_github_user_iri",
    "parse_infoscience_iri",
    "parse_orcid",
    "resolve_article_id",
    "resolve_organization_id",
    "resolve_person_id",
    "resolve_repository_id",
]
