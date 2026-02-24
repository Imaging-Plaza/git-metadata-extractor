"""Canonical ID resolution for v2 entities."""

from src.v2.canonicalization.id_resolution import (
    resolve_article_id,
    resolve_organization_id,
    resolve_person_id,
    resolve_repository_id,
)
from src.v2.canonicalization.organization_alias_map import (
    AliasResolution,
    OrganizationAliasResolver,
)
from src.v2.canonicalization.string_utils import normalize_string

__all__ = [
    "AliasResolution",
    "OrganizationAliasResolver",
    "normalize_string",
    "resolve_article_id",
    "resolve_organization_id",
    "resolve_person_id",
    "resolve_repository_id",
]
