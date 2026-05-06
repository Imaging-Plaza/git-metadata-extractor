"""Drop hallucinated `schema:ScholarlyArticle` entities.

The article LLM agent will sometimes invent an article entity for a repository
that has no actual publication backing it (placeholder DOIs like
``10.0000/foo``, repo-name-shaped titles, etc.). The retention rule is
straightforward: an article must carry **at least one verified identifier**:

- a `pulse:infoscienceArticleIdentifier` set to a non-empty value, OR
- a `schema:identifier` DOI that link veracity judged supported.

Articles that satisfy neither are pruned and added to ``excluded_entities``
with a clear reason.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from src.v2.pipeline.stages.models import AssembledOutput

ARTICLE_TYPE = "schema:ScholarlyArticle"

# IANA reserves the `10.0000/...` prefix for testing — any DOI starting there
# is by definition fake. We catch this even when link veracity hasn't run.
_PLACEHOLDER_DOI_PATTERN = re.compile(r"^10\.0000/", flags=re.IGNORECASE)
_DOI_HOSTS = ("doi.org", "dx.doi.org")


def _normalize_doi_url(value: Any) -> str | None:
    """Return the canonical `https://doi.org/<doi>` URL for a `schema:identifier`."""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ):
        if lowered.startswith(prefix):
            return f"https://doi.org/{candidate[len(prefix):]}"
    if candidate.startswith("10.") and "/" in candidate:
        return f"https://doi.org/{candidate}"
    return None


def _is_placeholder_doi(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/"):
        if candidate.lower().startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    return bool(_PLACEHOLDER_DOI_PATTERN.match(candidate))


def _supported_link_set(veracity_records: list[dict[str, Any]]) -> set[str]:
    supported: set[str] = set()
    for record in veracity_records:
        if not isinstance(record, dict):
            continue
        if record.get("status") != "ok":
            continue
        if not record.get("relationship_supported"):
            continue
        link = record.get("link")
        if isinstance(link, str) and link:
            supported.add(link)
    return supported


def _unsupported_link_set(veracity_records: list[dict[str, Any]]) -> set[str]:
    """Links the LLM/fetcher explicitly rejected (relationship not supported,
    or fetch failed). Self-reference-skipped contexts never enter this set."""

    unsupported: set[str] = set()
    for record in veracity_records:
        if not isinstance(record, dict):
            continue
        link = record.get("link")
        if not isinstance(link, str) or not link:
            continue
        status = record.get("status")
        if status == "ok" and record.get("relationship_supported") is False:
            unsupported.add(link)
        elif record.get("fetched_successfully") is False:
            unsupported.add(link)
    return unsupported


def _has_infoscience_identifier(article: dict[str, Any]) -> bool:
    direct = article.get("pulse:infoscienceArticleIdentifier")
    if isinstance(direct, str) and direct.strip():
        return True
    identifiers = article.get("identifiers")
    if isinstance(identifiers, dict):
        nested = identifiers.get("pulse:infoscienceArticleIdentifier")
        if isinstance(nested, str) and nested.strip():
            return True
    return False


def _excluded_record(article: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "entity_type": "article",
        "entity": deepcopy(article),
        "reason": [
            {
                "path": "<root>",
                "message": "article_validation_dropped",
                "constraint": "article_validation",
                "expected": reason,
            },
        ],
    }


def validate_articles(
    assembled: AssembledOutput,
    *,
    veracity_records: list[dict[str, Any]] | None = None,
) -> tuple[AssembledOutput, list[str]]:
    """Drop articles whose DOI evidence is provably bad.

    Retention policy (innocent until proven guilty):

    - Has a non-empty `pulse:infoscienceArticleIdentifier` → keep.
    - Has a placeholder DOI (`10.0000/...`) → drop.
    - Has a DOI link veracity **explicitly** marked unsupported / fetch-failed → drop.
    - Has no DOI and no infoscience id → drop.
    - Has a DOI that wasn't checked (e.g. self-reference skip) → keep.
    """

    unsupported_links = _unsupported_link_set(veracity_records or [])

    new_related: list[dict[str, Any]] = []
    excluded_entities = list(assembled.excluded_entities)
    warnings: list[str] = []

    for entity in assembled.related_entities:
        if not isinstance(entity, dict) or entity.get("type") != ARTICLE_TYPE:
            new_related.append(entity)
            continue

        article_id = entity.get("id") if isinstance(entity.get("id"), str) else None
        schema_identifier = entity.get("schema:identifier")

        # A non-empty infoscience identifier is sufficient evidence on its own.
        if _has_infoscience_identifier(entity):
            new_related.append(entity)
            continue

        # No infoscience backing — DOI must be present, non-placeholder, and
        # not explicitly rejected by link veracity.
        drop_reason: str | None = None
        if _is_placeholder_doi(schema_identifier):
            drop_reason = "Article has placeholder DOI (10.0000/* prefix)"
        else:
            doi_url = _normalize_doi_url(schema_identifier)
            if doi_url is None:
                drop_reason = "Article has no DOI and no infoscience identifier"
            elif doi_url in unsupported_links:
                drop_reason = "Article DOI explicitly failed link veracity and no infoscience identifier"

        if drop_reason is not None:
            excluded_entities.append(_excluded_record(entity, drop_reason))
            warnings.append(
                f"Removed article entity '{article_id or '<unknown>'}': {drop_reason}",
            )
            continue

        new_related.append(entity)

    updated = AssembledOutput(
        root_entity=assembled.root_entity,
        related_entities=new_related,
        excluded_entities=excluded_entities,
        warnings=list(assembled.warnings),
    )
    return updated, warnings


__all__ = ["validate_articles"]
