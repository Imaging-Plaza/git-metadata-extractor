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
from src.v2.canonicalization.string_utils import normalize_string, strip_accents

DEFAULT_QUERY_CAP = 8
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


def _lookup_token_variants(value: Any) -> list[str]:
    candidate = _as_string(value)
    if candidate is None:
        return []

    variants: list[str] = []
    seen: set[str] = set()

    def _add(token: str | None) -> None:
        if token is None:
            return
        normalized = token.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        variants.append(normalized)

    def _add_normalized_forms(token: str) -> None:
        lowered = token.casefold()
        _add(lowered)
        _add(strip_accents(lowered))
        _add(normalize_string(token))

    _add_normalized_forms(candidate)

    if "," in candidate:
        family, given = (segment.strip() for segment in candidate.split(",", maxsplit=1))
        if family and given:
            _add_normalized_forms(f"{given} {family}")

    return variants


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
    for normalized in _lookup_token_variants(token):
        lookup.setdefault(normalized, canonical_id)


def _resolve_lookup_token(lookup: dict[str, str], token: Any) -> str | None:
    for normalized in _lookup_token_variants(token):
        resolved = lookup.get(normalized)
        if isinstance(resolved, str):
            return resolved
    return None


def _register_organization_handle_tokens(
    lookup: dict[str, str],
    token: Any,
    canonical_id: str,
) -> None:
    handle = _as_string(token)
    if handle is None:
        return
    normalized = handle[1:] if handle.startswith("@") else handle
    if not normalized:
        return
    _register_lookup_token(lookup, normalized, canonical_id)
    _register_lookup_token(lookup, f"@{normalized}", canonical_id)
    _register_lookup_token(lookup, handle, canonical_id)


def _orcid_record_name(record: dict[str, Any]) -> str | None:
    name = _as_string(record.get("name"))
    if name:
        return name

    name_payload = record.get("name")
    if not isinstance(name_payload, dict):
        return None

    given_names_payload = name_payload.get("given-names")
    family_name_payload = name_payload.get("family-name")
    given_name = (
        _as_string(given_names_payload.get("value"))
        if isinstance(given_names_payload, dict)
        else None
    )
    family_name = (
        _as_string(family_name_payload.get("value"))
        if isinstance(family_name_payload, dict)
        else None
    )
    if given_name and family_name:
        return f"{given_name} {family_name}"
    return given_name or family_name


def _collect_person_alias_tokens(  # noqa: C901
    person: dict[str, Any],
    *,
    derivation_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    aliases: list[str] = []

    def _append(value: Any) -> None:
        candidate = _as_string(value)
        if candidate:
            aliases.append(candidate)

    person_id = _as_string(person.get("id"))

    for key in (
        "schema:name",
        "name",
        "display_name",
        "github_display_name",
        "github_name",
        "orcid_name",
        "infoscience_name",
        "pulse:githubUsername",
    ):
        _append(person.get(key))

    identifiers = person.get("identifiers")
    if isinstance(identifiers, dict):
        _append(identifiers.get("pulse:githubUsername"))

    github_profile = person.get("github_profile")
    if isinstance(github_profile, dict):
        _append(github_profile.get("name"))
        _append(github_profile.get("display_name"))
        _append(github_profile.get("login"))

    github_payload = person.get("github")
    if isinstance(github_payload, dict):
        _append(github_payload.get("name"))
        _append(github_payload.get("login"))

    orcid_record = person.get("orcid_record")
    if isinstance(orcid_record, dict):
        _append(_orcid_record_name(orcid_record))

    infoscience_record = person.get("infoscience_record")
    if isinstance(infoscience_record, dict):
        _append(infoscience_record.get("name"))
        _append(infoscience_record.get("displayName"))

    if person_id and person_id in derivation_by_id:
        derivation = derivation_by_id[person_id]
        for key in (
            "github_username",
            "github_display_name",
            "orcid_name",
            "infoscience_name",
            "person_name",
        ):
            _append(derivation.get(key))

    return _dedupe_preserve_order(aliases)


def _build_person_lookup(
    persons: list[dict[str, Any]],
    *,
    person_derivations: Any = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build the alias→person_id lookup plus an infoscience-authority→person_id map.

    The authority lookup is keyed on raw DSpace authority UUIDs (``pulse:infosciencePersonIdentifier``)
    so article authors can be mapped directly without name fuzzy-matching when DSpace returned
    an authority on the publication.
    """
    lookup: dict[str, str] = {}
    authority_lookup: dict[str, str] = {}
    derivation_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(person_derivations, list):
        for derivation in person_derivations:
            if not isinstance(derivation, dict):
                continue
            person_id = _as_string(derivation.get("person_id"))
            if person_id:
                derivation_by_id[person_id] = derivation

    for person in persons:
        person_id = _as_string(person.get("id"))
        if person_id is None:
            continue
        _register_lookup_token(lookup, person_id, person_id)

        for alias_token in _collect_person_alias_tokens(
            person,
            derivation_by_id=derivation_by_id,
        ):
            _register_lookup_token(lookup, alias_token, person_id)

        infoscience_id = _as_string(person.get("pulse:infosciencePersonIdentifier"))
        if infoscience_id is None:
            identifiers = person.get("identifiers")
            if isinstance(identifiers, dict):
                infoscience_id = _as_string(
                    identifiers.get("pulse:infosciencePersonIdentifier"),
                )
        if infoscience_id:
            authority_lookup.setdefault(infoscience_id, person_id)
    return lookup, authority_lookup


def _build_organization_lookup(organizations: list[dict[str, Any]]) -> dict[str, str]:  # noqa: C901
    lookup: dict[str, str] = {}
    for organization in organizations:
        organization_id = _as_string(organization.get("id"))
        if organization_id is None:
            continue
        _register_lookup_token(lookup, organization_id, organization_id)
        _register_lookup_token(lookup, organization.get("schema:name"), organization_id)
        _register_organization_handle_tokens(
            lookup,
            organization.get("pulse:githubOrganizationHandle"),
            organization_id,
        )
        _register_lookup_token(lookup, organization.get("schema:identifier"), organization_id)
        for key in ("aliases", "acronyms"):
            values = organization.get(key)
            if isinstance(values, list):
                for value in values:
                    _register_lookup_token(lookup, value, organization_id)
        labels = organization.get("labels")
        if isinstance(labels, list):
            for label_payload in labels:
                label = label_payload
                if isinstance(label_payload, dict):
                    label = label_payload.get("label")
                _register_lookup_token(lookup, label, organization_id)

        identifiers = organization.get("identifiers")
        if isinstance(identifiers, dict):
            _register_organization_handle_tokens(
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
    authority_lookup: dict[str, str],
    publication_reference: str,
) -> tuple[list[str], list[str], list[str], int, int]:
    warnings: list[str] = []
    unresolved_authors: list[str] = []
    mapped_authors: list[str] = []
    matched_authors = 0
    unresolved_count = 0
    author_names = _as_string_list(publication.get("authors"))
    raw_authorities = publication.get("author_authorities")
    authority_values: list[str | None]
    if isinstance(raw_authorities, list) and len(raw_authorities) == len(author_names):
        authority_values = [
            item if isinstance(item, str) and item.strip() else None
            for item in raw_authorities
        ]
    else:
        authority_values = [None] * len(author_names)

    for author_name, authority in zip(author_names, authority_values, strict=False):
        resolved_author: str | None = None
        if authority is not None:
            resolved_author = authority_lookup.get(authority)
        if resolved_author is None:
            resolved_author = _resolve_lookup_token(person_lookup, author_name)
        if isinstance(resolved_author, str):
            mapped_authors.append(resolved_author)
            matched_authors += 1
            continue
        unresolved_authors.append(author_name)
        unresolved_count += 1

    if not mapped_authors:
        raw_author_values = _as_string_list(publication.get("authors"))
        author_preview = (
            ", ".join(f"'{author}'" for author in raw_author_values[:3])
            if raw_author_values
            else "<no-author-names>"
        )
        _append_unique(
            warnings,
            (
                "Publication has no resolvable author identifiers: "
                f"{publication_reference} (author_examples={author_preview})"
            ),
        )

    return (
        _dedupe_preserve_order(mapped_authors),
        _dedupe_preserve_order(unresolved_authors),
        warnings,
        matched_authors,
        unresolved_count,
    )


def _map_source_organization(
    publication: dict[str, Any],
    *,
    organization_lookup: dict[str, str],
) -> tuple[str | None, list[str]]:
    """Map an article's `sourceOrganization` to a canonical in-graph
    Organization id.

    When the lookup misses, return `None` (drop the field) rather than
    leaking the raw query string as a dangling reference. Emitting
    `schema:sourceOrganization = "ETH Zurich"` when no `ETH Zurich`
    Organization exists in the graph creates a SHACL-orphan pointer that
    no consumer can dereference. The deterministic rule: emit the field
    only when we can resolve it to an entity we know.
    """
    source_organization = _as_string(publication.get("sourceOrganization"))
    if source_organization is None:
        return None, []

    resolved_source = _resolve_lookup_token(organization_lookup, source_organization)
    if isinstance(resolved_source, str):
        return resolved_source, []

    return None, [
        f"Dropped article source organization {source_organization!r}: "
        "no matching Organization in the assembled graph.",
    ]


def _normalize_publication_date(
    publication_date: Any,
) -> tuple[str | None, str | None]:
    normalized = _as_string(publication_date)
    if normalized and ISO_DATE_PATTERN.fullmatch(normalized):
        return normalized, None
    if normalized and YEAR_ONLY_DATE_PATTERN.fullmatch(normalized):
        normalized_year_date = f"{normalized}-01-01"
        return (
            normalized_year_date,
            (
                "Publication date provided as year-only; normalized to "
                f"'{normalized_year_date}' for schema compatibility"
            ),
        )
    if normalized:
        return None, f"Publication date '{normalized}' is invalid"
    return None, "Publication date is missing"


def _publication_label(publication: dict[str, Any]) -> str:
    for key in ("doi", "infosciencePublicationIdentifier", "title", "url"):
        value = _as_string(publication.get(key))
        if value:
            return value
    return "<unknown-article>"


def _publication_reference(publication: dict[str, Any]) -> str:
    parts: list[str] = []
    doi = _as_string(publication.get("doi"))
    infoscience_id = _as_string(publication.get("infosciencePublicationIdentifier"))
    title = _as_string(publication.get("title"))
    url = _as_string(publication.get("url"))

    if doi:
        parts.append(f"doi={doi}")
    if infoscience_id:
        parts.append(f"infoscience={infoscience_id}")
    if title:
        parts.append(f"title='{title}'")
    if url:
        parts.append(f"url={url}")

    if parts:
        return ", ".join(parts)
    return "<unknown-article>"


def _build_article_payload(
    publication: dict[str, Any],
    *,
    author_ids: list[str],
    source_organization: str | None,
    publication_date: str,
    article_uuid: str,
) -> dict[str, Any]:
    from src.v2.canonicalization import doi_iri, infoscience_article_iri

    raw_doi = _as_string(publication.get("doi"))
    # v2.2.0: every DOI lands in canonical `https://doi.org/<bare>`
    # form. Tolerates bare / `doi:` / legacy `dx.doi.org` / canonical
    # URL on the way in (catalog backends produce mixed shapes
    # depending on the provider source).
    doi = doi_iri(raw_doi)
    # v2.2.0: Infoscience IDs also canonical URL form.
    infoscience_id = infoscience_article_iri(
        _as_string(publication.get("infosciencePublicationIdentifier")),
    )

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
    """Article agent with deterministic query blending, ranking, and linkage mapping.

    The default query blend is **repo-name only** (the repository's GitHub
    handle, slug, owner). This is narrow on purpose: when we widen the blend
    to also search by every contributor's name and every org's name, the
    Infoscience search returns those people's *entire* bibliography, and
    the result is that publications unrelated to the repo get attributed to
    it (real DOIs, false attribution). The original intent of the wider
    blend was to recover citation papers, but in practice the over-attribution
    rate dwarfs the recall benefit.

    Set `include_person_queries=True` and/or `include_organization_queries=True`
    explicitly when running on a single-author / single-lab repo where the
    over-attribution risk is low. Default to repo-only.
    """

    def __init__(
        self,
        *,
        max_queries: int = DEFAULT_QUERY_CAP,
        include_person_queries: bool = False,
        include_organization_queries: bool = False,
    ) -> None:
        self._max_queries = max_queries
        self._include_person_queries = include_person_queries
        self._include_organization_queries = include_organization_queries

    def build_query_blend(self, context: dict[str, Any]) -> list[str]:
        # Per-request override wins over the constructor default. Useful for
        # CLI / test cases that want a wider blend without rebuilding the
        # agent.
        include_person = bool(
            context.get("include_person_queries", self._include_person_queries),
        )
        include_org = bool(
            context.get(
                "include_organization_queries",
                self._include_organization_queries,
            ),
        )

        query_terms = _extract_root_terms(context)
        if include_person:
            query_terms.extend(
                _extract_person_terms(_collect_known_persons(context)),
            )
        if include_org:
            query_terms.extend(
                _extract_organization_terms(_collect_known_organizations(context)),
            )
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

        person_lookup, authority_lookup = _build_person_lookup(
            _collect_known_persons(context),
            person_derivations=context.get("person_derivations"),
        )
        organization_lookup = _build_organization_lookup(_collect_known_organizations(context))

        ranked_candidates = _rank_and_dedupe_publications(raw_candidates)
        validated_articles: list[dict[str, Any]] = []
        raw_articles: list[dict[str, Any]] = []
        unresolved_author_names: list[str] = []
        skipped_missing_resolvable_author_candidates: list[tuple[str, int, int]] = []
        reported_unresolvable_author_identifiers_warning = False

        for candidate in ranked_candidates:
            publication_reference = _publication_reference(candidate.publication)
            (
                author_ids,
                unresolved_authors,
                author_warnings,
                matched_author_count,
                unresolved_author_count,
            ) = _map_author_ids(
                candidate.publication,
                person_lookup=person_lookup,
                authority_lookup=authority_lookup,
                publication_reference=publication_reference,
            )
            for unresolved_author in unresolved_authors:
                if unresolved_author not in unresolved_author_names:
                    unresolved_author_names.append(unresolved_author)
            for warning in author_warnings:
                if warning.startswith("Publication has no resolvable author identifiers:"):
                    if reported_unresolvable_author_identifiers_warning:
                        continue
                    reported_unresolvable_author_identifiers_warning = True
                _append_unique(warnings, warning)

            if not author_ids:
                skipped_missing_resolvable_author_candidates.append(
                    (
                        publication_reference,
                        matched_author_count,
                        unresolved_author_count,
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
                        "Skipped article candidate due to invalid publication date: "
                        f"{publication_reference}"
                    ),
                )
                continue

            if not _as_string(candidate.publication.get("doi")):
                _append_unique(
                    warnings,
                    (
                        "Skipped article candidate due to missing DOI required by strict schema: "
                        f"{publication_reference}"
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
            _append_unique(
                warnings,
                (
                    "Dropped unresolved article author references for "
                    f"{len(unresolved_author_names)} name(s). "
                    f"Examples: {preview}{remainder_suffix}"
                ),
            )

        if skipped_missing_resolvable_author_candidates:
            if len(skipped_missing_resolvable_author_candidates) == 1:
                publication_label, matched_author_count, unresolved_author_count = (
                    skipped_missing_resolvable_author_candidates[0]
                )
                _append_unique(
                    warnings,
                    (
                        "Skipped article candidate due to missing resolvable authors: "
                        f"{publication_label} "
                        f"(matched_authors={matched_author_count}, "
                        f"unmatched_authors={unresolved_author_count})"
                    ),
                )
            else:
                example_count = min(max_unresolved_authors, 5)
                examples = ", ".join(
                    (
                        f"'{label}' (matched_authors={matched_author_count}, "
                        f"unmatched_authors={unresolved_author_count})"
                    )
                    for label, matched_author_count, unresolved_author_count in skipped_missing_resolvable_author_candidates[:example_count]
                )
                remainder = len(skipped_missing_resolvable_author_candidates) - example_count
                remainder_suffix = f", +{remainder} more" if remainder > 0 else ""
                _append_unique(
                    warnings,
                    (
                        "Skipped article candidates due to missing resolvable authors: "
                        f"count={len(skipped_missing_resolvable_author_candidates)}. "
                        f"Examples: {examples}{remainder_suffix}"
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
