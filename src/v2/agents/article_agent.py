from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from src.v2.agents.models import (
    AgentResult,
    ProviderSet,
    generate_uuid,
    validate_permissive,
)

DEFAULT_QUERY_CAP = 8
UNKNOWN_AUTHOR = "unknown-author"
UNKNOWN_ARTICLE_DATE = "1900-01-01"
DOI_PREFIX = "doi"
INFOSCIENCE_PREFIX = "infoscience"
URL_PREFIX = "url"
TITLE_DATE_PREFIX = "title-date"
DEFAULT_MAX_UNRESOLVED_AUTHORS = 10
IDENTITY_ORDER = {
    DOI_PREFIX: 0,
    INFOSCIENCE_PREFIX: 1,
    URL_PREFIX: 2,
    TITLE_DATE_PREFIX: 3,
}
YEAR_PATTERN = re.compile(r"^(?P<year>\d{4})")
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YEAR_ONLY_DATE_PATTERN = re.compile(r"^\d{4}$")
MIN_REPOSITORY_PATH_SEGMENTS = 2


@dataclass(slots=True)
class _PublicationCandidate:
    publication: dict[str, Any]
    query: str
    query_index: int


def _as_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _normalize_token(value: Any) -> str | None:
    candidate = _as_string(value)
    return candidate.casefold() if candidate else None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = _normalize_token(value)
        if token is None or token in seen:
            continue
        deduplicated.append(value.strip())
        seen.add(token)
    return deduplicated


def _append_unique(target: list[str], warning: str) -> None:
    if warning and warning not in target:
        target.append(warning)


def _is_pipeline_key(candidate: str, prefix: str) -> bool:
    normalized = candidate.strip().lower()
    return normalized == prefix or normalized.startswith(f"{prefix}:")


def _collect_pipeline_entities(
    context: dict[str, Any],
    *,
    prefix: str,
) -> list[dict[str, Any]]:
    pipeline_outputs = context.get("pipeline_outputs")
    if not isinstance(pipeline_outputs, dict):
        return []

    entities: list[dict[str, Any]] = []
    for key, value in pipeline_outputs.items():
        if not isinstance(key, str) or not _is_pipeline_key(key, prefix):
            continue
        if isinstance(value, dict) and value:
            entities.append(value)
    return entities


def _collect_known_persons(context: dict[str, Any]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for key in ("known_persons", "persons"):
        value = context.get(key)
        if isinstance(value, list):
            entities.extend(
                item for item in value if isinstance(item, dict) and item
            )
    entities.extend(_collect_pipeline_entities(context, prefix="person_agent"))
    return entities


def _collect_known_organizations(context: dict[str, Any]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for key in ("known_organizations", "organizations"):
        value = context.get(key)
        if isinstance(value, list):
            entities.extend(
                item for item in value if isinstance(item, dict) and item
            )
    entities.extend(_collect_pipeline_entities(context, prefix="org_agent"))
    return entities


def _register_lookup_token(lookup: dict[str, str], token: Any, canonical_id: str) -> None:
    normalized = _normalize_token(token)
    if normalized is None:
        return
    lookup.setdefault(normalized, canonical_id)


def _build_person_lookup(persons: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for person in persons:
        person_id = _as_string(person.get("id"))
        if person_id is None:
            continue
        _register_lookup_token(lookup, person_id, person_id)
        _register_lookup_token(lookup, person.get("schema:name"), person_id)
        _register_lookup_token(lookup, person.get("pulse:githubUsername"), person_id)

        identifiers = person.get("identifiers")
        if isinstance(identifiers, dict):
            _register_lookup_token(
                lookup,
                identifiers.get("pulse:githubUsername"),
                person_id,
            )
    return lookup


def _build_organization_lookup(organizations: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for organization in organizations:
        organization_id = _as_string(organization.get("id"))
        if organization_id is None:
            continue
        _register_lookup_token(lookup, organization_id, organization_id)
        _register_lookup_token(lookup, organization.get("schema:name"), organization_id)
        _register_lookup_token(
            lookup,
            organization.get("pulse:githubOrganizationHandle"),
            organization_id,
        )
        _register_lookup_token(lookup, organization.get("schema:identifier"), organization_id)

        identifiers = organization.get("identifiers")
        if isinstance(identifiers, dict):
            _register_lookup_token(
                lookup,
                identifiers.get("pulse:githubOrganizationHandle"),
                organization_id,
            )
            _register_lookup_token(
                lookup,
                identifiers.get("pulse:ror"),
                organization_id,
            )
    return lookup


def _extract_root_terms(context: dict[str, Any]) -> list[str]:  # noqa: C901
    detected_type = _normalize_token(context.get("detected_type"))
    terms: list[str] = []

    if detected_type == "repository":
        repository_handle = _as_string(
            context.get("full_name")
            or context.get("repository_handle")
            or context.get("github_repository_handle"),
        )
        repository_context = context.get("repository_context")
        if repository_handle is None and isinstance(repository_context, dict):
            repository_handle = _as_string(repository_context.get("full_name"))
        if repository_handle:
            terms.append(repository_handle)
            if "/" in repository_handle:
                terms.append(repository_handle.rsplit("/", maxsplit=1)[-1])

    if detected_type == "user":
        username = _as_string(context.get("username") or context.get("github_username"))
        if username:
            terms.append(username)

    if detected_type == "organization":
        org_name = _as_string(
            context.get("org_name")
            or context.get("organization")
            or context.get("github_organization_handle"),
        )
        if org_name:
            terms.append(org_name)

    source_url = _as_string(context.get("source_url"))
    if source_url:
        parsed = urlparse(source_url if "://" in source_url else f"https://{source_url}")
        path_segments = [segment for segment in parsed.path.split("/") if segment]
        if len(path_segments) >= 1 and detected_type in {"user", "organization"}:
            terms.append(path_segments[0])
        if len(path_segments) >= MIN_REPOSITORY_PATH_SEGMENTS:
            terms.append(f"{path_segments[0]}/{path_segments[1]}")
            terms.append(path_segments[1])

    return _dedupe_preserve_order(terms)


def _extract_person_terms(persons: list[dict[str, Any]]) -> list[str]:
    terms: list[str] = []
    for person in persons:
        name = _as_string(person.get("schema:name"))
        if name:
            terms.append(name)
        github_username = _as_string(person.get("pulse:githubUsername"))
        if github_username:
            terms.append(github_username)
    return _dedupe_preserve_order(terms)


def _extract_organization_terms(organizations: list[dict[str, Any]]) -> list[str]:
    terms: list[str] = []
    for organization in organizations:
        name = _as_string(organization.get("schema:name"))
        if name:
            terms.append(name)
        github_handle = _as_string(organization.get("pulse:githubOrganizationHandle"))
        if github_handle:
            terms.append(github_handle)
    return _dedupe_preserve_order(terms)


def _publication_identity(publication: dict[str, Any]) -> tuple[str, str]:
    doi = _normalize_token(publication.get("doi"))
    if doi:
        return DOI_PREFIX, doi

    infoscience_id = _normalize_token(publication.get("infosciencePublicationIdentifier"))
    if infoscience_id:
        return INFOSCIENCE_PREFIX, infoscience_id

    url = _normalize_token(publication.get("url"))
    if url:
        return URL_PREFIX, url

    title = _normalize_token(publication.get("title")) or "untitled"
    date = _normalize_token(publication.get("publicationDate")) or ""
    return TITLE_DATE_PREFIX, f"{title}|{date}"


def _publication_year(publication: dict[str, Any]) -> int:
    publication_date = _as_string(publication.get("publicationDate"))
    if publication_date is None:
        return 0
    year_match = YEAR_PATTERN.match(publication_date)
    if year_match is None:
        return 0
    return int(year_match.group("year"))


def _candidate_rank_key(candidate: _PublicationCandidate) -> tuple[Any, ...]:
    publication = candidate.publication
    score = publication.get("score")
    numeric_score = float(score) if isinstance(score, (int, float)) else 0.0
    has_doi = 1.0 if _as_string(publication.get("doi")) else 0.0
    has_infoscience_id = (
        1.0 if _as_string(publication.get("infosciencePublicationIdentifier")) else 0.0
    )
    has_source_org = 1.0 if _as_string(publication.get("sourceOrganization")) else 0.0
    author_count = float(len(_as_string_list(publication.get("authors"))))
    query_priority_bonus = max(0.0, 10.0 - float(candidate.query_index))
    total_score = (
        numeric_score
        + (has_doi * 100.0)
        + (has_infoscience_id * 40.0)
        + (has_source_org * 15.0)
        + author_count
        + query_priority_bonus
    )
    identity_prefix, identity_value = _publication_identity(publication)
    title = _normalize_token(publication.get("title")) or ""

    return (
        -total_score,
        -_publication_year(publication),
        candidate.query_index,
        IDENTITY_ORDER[identity_prefix],
        identity_value,
        title,
    )


def _rank_and_dedupe_publications(
    candidates: list[_PublicationCandidate],
) -> list[_PublicationCandidate]:
    sorted_candidates = sorted(candidates, key=_candidate_rank_key)
    deduped: list[_PublicationCandidate] = []
    seen_identities: set[tuple[str, str]] = set()
    for candidate in sorted_candidates:
        identity = _publication_identity(candidate.publication)
        if identity in seen_identities:
            continue
        deduped.append(candidate)
        seen_identities.add(identity)
    return deduped


def _map_author_ids(
    publication: dict[str, Any],
    *,
    person_lookup: dict[str, str],
    allow_synthetic_fallbacks: bool,
) -> tuple[list[str], list[str], list[str]]:
    warnings: list[str] = []
    unresolved_authors: list[str] = []
    mapped_authors: list[str] = []
    for author_name in _as_string_list(publication.get("authors")):
        normalized_author = _normalize_token(author_name)
        if normalized_author and normalized_author in person_lookup:
            mapped_authors.append(person_lookup[normalized_author])
            continue
        unresolved_authors.append(author_name)
        if allow_synthetic_fallbacks:
            mapped_authors.append(author_name)

    if not mapped_authors:
        if allow_synthetic_fallbacks:
            mapped_authors = [UNKNOWN_AUTHOR]
            _append_unique(
                warnings,
                "Publication has no author names; using placeholder author identifier",
            )
        else:
            _append_unique(
                warnings,
                "Publication has no resolvable author identifiers",
            )

    return (
        _dedupe_preserve_order(mapped_authors),
        _dedupe_preserve_order(unresolved_authors),
        warnings,
    )


def _map_source_organization(
    publication: dict[str, Any],
    *,
    organization_lookup: dict[str, str],
) -> tuple[str | None, list[str]]:
    source_organization = _as_string(publication.get("sourceOrganization"))
    if source_organization is None:
        return None, []

    normalized_source = _normalize_token(source_organization)
    if normalized_source and normalized_source in organization_lookup:
        return organization_lookup[normalized_source], []

    return source_organization, [
        f"Unresolved article source organization mapping: '{source_organization}'",
    ]


def _normalize_publication_date(
    publication_date: Any,
    *,
    allow_synthetic_fallbacks: bool,
) -> tuple[str | None, str | None]:
    normalized = _as_string(publication_date)
    if normalized and ISO_DATE_PATTERN.fullmatch(normalized):
        return normalized, None
    if normalized and YEAR_ONLY_DATE_PATTERN.fullmatch(normalized):
        if allow_synthetic_fallbacks:
            normalized_year_date = f"{normalized}-01-01"
            return (
                normalized_year_date,
                (
                    "Publication date provided as year-only; normalized to "
                    f"'{normalized_year_date}' for schema compatibility"
                ),
            )
        return None, f"Publication date '{normalized}' is year-only and cannot be used without synthetic fallback"
    if allow_synthetic_fallbacks:
        return UNKNOWN_ARTICLE_DATE, "Publication date missing/invalid; using placeholder date"
    if normalized:
        return None, f"Publication date '{normalized}' is invalid and synthetic fallback is disabled"
    return None, "Publication date is missing and synthetic fallback is disabled"


def _publication_label(publication: dict[str, Any]) -> str:
    for key in ("doi", "infosciencePublicationIdentifier", "title", "url"):
        value = _as_string(publication.get(key))
        if value:
            return value
    return "<unknown-article>"


def _build_article_payload(
    publication: dict[str, Any],
    *,
    author_ids: list[str],
    source_organization: str | None,
    publication_date: str,
    article_uuid: str,
) -> dict[str, Any]:
    doi = _as_string(publication.get("doi"))
    infoscience_id = _as_string(publication.get("infosciencePublicationIdentifier"))

    identifier_value = doi or infoscience_id or _as_string(publication.get("url")) or article_uuid
    if doi:
        article_id = doi
        id_source = "schema:identifier"
    elif infoscience_id:
        article_id = infoscience_id
        id_source = "pulse:infoscienceArticleIdentifier"
    else:
        article_id = article_uuid
        id_source = "uuid"

    return {
        "id": article_id,
        "type": "schema:ScholarlyArticle",
        "shacl": "pulse:ArticleShape",
        "identifiers": {
            "schema:identifier": doi,
            "pulse:infoscienceArticleIdentifier": infoscience_id,
            "uuid": article_uuid,
        },
        "idSource": id_source,
        "schema:name": _as_string(publication.get("title")) or "Untitled article",
        "schema:identifier": identifier_value,
        "schema:datePublished": publication_date,
        "schema:author": author_ids,
        "pulse:infoscienceArticleIdentifier": infoscience_id,
        "schema:sourceOrganization": source_organization,
    }


class ArticleAgentV2:
    """Article agent with deterministic query blending, ranking, and linkage mapping."""

    def __init__(self, *, max_queries: int = DEFAULT_QUERY_CAP) -> None:
        self._max_queries = max_queries

    def build_query_blend(self, context: dict[str, Any]) -> list[str]:
        persons = _collect_known_persons(context)
        organizations = _collect_known_organizations(context)

        query_terms = _extract_root_terms(context)
        query_terms.extend(_extract_person_terms(persons))
        query_terms.extend(_extract_organization_terms(organizations))
        query_terms = _dedupe_preserve_order(query_terms)
        if self._max_queries <= 0:
            return []
        return query_terms[: self._max_queries]

    async def run(  # noqa: C901, PLR0912, PLR0915
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        warnings: list[str] = []
        queries = self.build_query_blend(context)
        allow_synthetic_fallbacks = context.get("allow_synthetic_fallbacks")
        if not isinstance(allow_synthetic_fallbacks, bool):
            allow_synthetic_fallbacks = True

        max_unresolved_authors = context.get("max_unresolved_article_authors")
        if not isinstance(max_unresolved_authors, int) or max_unresolved_authors < 1:
            max_unresolved_authors = DEFAULT_MAX_UNRESOLVED_AUTHORS

        if providers.infoscience is None:
            return AgentResult(
                data={},
                warnings=["Infoscience provider not configured for article enrichment"],
                raw_output={},
                stats={"queries": queries, "articles": []},
            )

        if not queries:
            return AgentResult(
                data={},
                warnings=["No article queries could be derived from runtime context"],
                raw_output={},
                stats={"queries": [], "articles": []},
            )

        raw_candidates: list[_PublicationCandidate] = []
        for query_index, query in enumerate(queries):
            try:
                publications = providers.infoscience.search_publications(query)
            except Exception as exc:  # noqa: BLE001
                _append_unique(warnings, f"Infoscience publication search failed for '{query}': {exc}")
                continue

            raw_candidates.extend(
                _PublicationCandidate(
                    publication=dict(publication),
                    query=query,
                    query_index=query_index,
                )
                for publication in publications
                if isinstance(publication, dict)
            )

        if not raw_candidates:
            _append_unique(warnings, "No publications returned for blended article queries")
            return AgentResult(
                data={},
                warnings=warnings,
                raw_output={},
                stats={"queries": queries, "articles": []},
            )

        person_lookup = _build_person_lookup(_collect_known_persons(context))
        organization_lookup = _build_organization_lookup(_collect_known_organizations(context))

        ranked_candidates = _rank_and_dedupe_publications(raw_candidates)
        validated_articles: list[dict[str, Any]] = []
        raw_articles: list[dict[str, Any]] = []
        unresolved_author_names: list[str] = []

        for candidate in ranked_candidates:
            author_ids, unresolved_authors, author_warnings = _map_author_ids(
                candidate.publication,
                person_lookup=person_lookup,
                allow_synthetic_fallbacks=allow_synthetic_fallbacks,
            )
            for unresolved_author in unresolved_authors:
                if unresolved_author not in unresolved_author_names:
                    unresolved_author_names.append(unresolved_author)
            for warning in author_warnings:
                _append_unique(warnings, warning)

            if not author_ids and not allow_synthetic_fallbacks:
                _append_unique(
                    warnings,
                    (
                        "Skipped article candidate due to missing resolvable authors with synthetic "
                        f"fallbacks disabled: {_publication_label(candidate.publication)}"
                    ),
                )
                continue

            source_organization, org_warnings = _map_source_organization(
                candidate.publication,
                organization_lookup=organization_lookup,
            )
            for warning in org_warnings:
                _append_unique(warnings, warning)

            publication_date, date_warning = _normalize_publication_date(
                candidate.publication.get("publicationDate"),
                allow_synthetic_fallbacks=allow_synthetic_fallbacks,
            )
            if date_warning:
                _append_unique(
                    warnings,
                    f"{_publication_label(candidate.publication)}: {date_warning}",
                )
            if publication_date is None:
                _append_unique(
                    warnings,
                    (
                        "Skipped article candidate due to invalid publication date with synthetic "
                        f"fallbacks disabled: {_publication_label(candidate.publication)}"
                    ),
                )
                continue

            payload = _build_article_payload(
                candidate.publication,
                author_ids=author_ids,
                source_organization=source_organization,
                publication_date=publication_date,
                article_uuid=generate_uuid(),
            )
            raw_payload = deepcopy(payload)
            validated_payload, validation_warnings = validate_permissive(
                payload,
                schema_name="article",
            )
            for warning in validation_warnings:
                _append_unique(
                    warnings,
                    f"{validated_payload.get('id', 'article')}: {warning}",
                )
            validated_articles.append(validated_payload)
            raw_articles.append(raw_payload)

        if unresolved_author_names:
            preview = ", ".join(f"'{name}'" for name in unresolved_author_names[:max_unresolved_authors])
            remainder = len(unresolved_author_names) - max_unresolved_authors
            remainder_suffix = f", +{remainder} more" if remainder > 0 else ""
            if allow_synthetic_fallbacks:
                _append_unique(
                    warnings,
                    (
                        "Deferred article author resolution for "
                        f"{len(unresolved_author_names)} name(s); reconciliation will synthesize "
                        f"fallback person links. Examples: {preview}{remainder_suffix}"
                    ),
                )
            else:
                _append_unique(
                    warnings,
                    (
                        "Dropped unresolved article author references for "
                        f"{len(unresolved_author_names)} name(s) because synthetic fallbacks are disabled. "
                        f"Examples: {preview}{remainder_suffix}"
                    ),
                )

        primary_article = validated_articles[0] if validated_articles else {}
        primary_raw_output = raw_articles[0] if raw_articles else {}
        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict) and primary_article:
            primary_article = {**primary_article, **overrides}
            primary_raw_output = deepcopy(primary_article)
            primary_article, override_warnings = validate_permissive(
                primary_article,
                schema_name="article",
            )
            for warning in override_warnings:
                _append_unique(
                    warnings,
                    f"{primary_article.get('id', 'article')}: {warning}",
                )
            validated_articles[0] = primary_article

        return AgentResult(
            data=primary_article,
            warnings=warnings,
            raw_output=primary_raw_output,
            stats={
                "queries": list(queries),
                "raw_candidate_count": len(raw_candidates),
                "ranked_candidate_count": len(ranked_candidates),
                "articles": deepcopy(validated_articles),
            },
        )
