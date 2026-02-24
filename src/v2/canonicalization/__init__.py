"""Canonical ID resolution for v2 entities."""

from src.v2.canonicalization.id_resolution import (
    resolve_article_id,
    resolve_organization_id,
    resolve_person_id,
    resolve_repository_id,
)

__all__ = [
    "resolve_article_id",
    "resolve_organization_id",
    "resolve_person_id",
    "resolve_repository_id",
]
