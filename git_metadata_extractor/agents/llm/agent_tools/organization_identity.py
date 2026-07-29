from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

from git_metadata_extractor.canonicalization.string_utils import normalize_string
from git_metadata_extractor.observation.query_log import record_query

if TYPE_CHECKING:
    from git_metadata_extractor.providers.base import InfoscienceProvider, RORProvider

logger = logging.getLogger(__name__)

MAX_QUERY_EXPANSIONS_PER_PROVIDER = 3
MAX_PROVIDER_CANDIDATES = 12
MAX_LINKED_CANDIDATES = 10
ORGANIZATION_NAME_EQUIVALENCE_TOKENS: dict[str, str] = {
    "centre": "center",
    "centres": "centers",
}


def _normalize_org_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = normalize_string(value)
    if not normalized:
        return None
    normalized_tokens = [
        ORGANIZATION_NAME_EQUIVALENCE_TOKENS.get(token, token)
        for token in normalized.split()
    ]
    canonical_name = " ".join(normalized_tokens)
    return canonical_name or None


def _dedupe_candidates(
    candidates: list[dict[str, Any]],
    *,
    key_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        marker = ""
        for field in key_fields:
            value = candidate.get(field)
            if isinstance(value, str) and value:
                marker = f"{field}:{value}"
                break
        if not marker:
            name_key = _normalize_org_name(candidate.get("name")) or repr(sorted(candidate.items()))
            marker = f"name:{name_key}"
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(candidate)
    return deduped


def _candidate_query_seeds(candidate: dict[str, Any], *, include_aliases: bool) -> list[str]:
    seeds: list[str] = []
    for key in ("name", "acronym", "parentOrganization"):
        value = candidate.get(key)
        if isinstance(value, str) and value:
            seeds.append(value)
    if include_aliases:
        aliases = candidate.get("aliases")
        if isinstance(aliases, list):
            seeds.extend(alias for alias in aliases if isinstance(alias, str) and alias)
        acronyms = candidate.get("acronyms")
        if isinstance(acronyms, list):
            seeds.extend(acronym for acronym in acronyms if isinstance(acronym, str) and acronym)
    return seeds


def _linked_candidates(
    ror_candidates: list[dict[str, Any]],
    infoscience_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    linked: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for ror_candidate in ror_candidates:
        ror_name = ror_candidate.get("name")
        ror_name_key = _normalize_org_name(ror_name)
        ror_id = ror_candidate.get("id")
        if not isinstance(ror_name_key, str):
            continue
        for infoscience_candidate in infoscience_candidates:
            infoscience_name = infoscience_candidate.get("name")
            infoscience_name_key = _normalize_org_name(infoscience_name)
            infoscience_id = infoscience_candidate.get("infoscienceOrgUnitIdentifier")
            if not isinstance(infoscience_name_key, str):
                continue
            if ror_name_key != infoscience_name_key:
                continue
            if not isinstance(ror_id, str) or not isinstance(infoscience_id, str):
                continue
            pair_key = (ror_id, infoscience_id)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            linked.append(
                {
                    "match_reason": "normalized_name_match",
                    "normalized_name": ror_name_key,
                    "ror_id": ror_id,
                    "ror_name": ror_name,
                    "infoscience_orgunit_identifier": infoscience_id,
                    "infoscience_name": infoscience_name,
                },
            )
    return linked


def make_organization_identity_search_tool(
    ror_provider: RORProvider,
    infoscience_provider: InfoscienceProvider,
) -> Tool:  # noqa: C901
    """Create a dual-provider organization lookup tool with lightweight cross-linking."""

    def search_organization_identity(query: str) -> dict[str, Any]:  # noqa: C901
        """Search both ROR and Infoscience and return candidate sets plus linked matches."""

        normalized_query = query.strip()
        logger.info("tool call: search_organization_identity — query=%r", normalized_query)
        if normalized_query:
            record_query(
                service="organization_identity.search",
                query=normalized_query,
            )
        if not normalized_query:
            return {
                "query": "",
                "ror_candidates": [],
                "infoscience_candidates": [],
                "linked_candidates": [],
                "expanded_queries": {
                    "ror": [],
                    "infoscience": [],
                },
            }

        ror_candidates = list(ror_provider.search_organizations(normalized_query))
        infoscience_candidates = list(infoscience_provider.search_orgunit(normalized_query))

        expanded_ror_queries: list[str] = []
        expanded_infoscience_queries: list[str] = []

        for ror_candidate in ror_candidates[:MAX_QUERY_EXPANSIONS_PER_PROVIDER]:
            for seed in _candidate_query_seeds(ror_candidate, include_aliases=True):
                if seed == normalized_query or seed in expanded_infoscience_queries:
                    continue
                expanded_infoscience_queries.append(seed)
                infoscience_candidates.extend(infoscience_provider.search_orgunit(seed))
                if len(expanded_infoscience_queries) >= MAX_QUERY_EXPANSIONS_PER_PROVIDER:
                    break
            if len(expanded_infoscience_queries) >= MAX_QUERY_EXPANSIONS_PER_PROVIDER:
                break

        for infoscience_candidate in infoscience_candidates[:MAX_QUERY_EXPANSIONS_PER_PROVIDER]:
            for seed in _candidate_query_seeds(infoscience_candidate, include_aliases=False):
                if seed == normalized_query or seed in expanded_ror_queries:
                    continue
                expanded_ror_queries.append(seed)
                ror_candidates.extend(ror_provider.search_organizations(seed))
                if len(expanded_ror_queries) >= MAX_QUERY_EXPANSIONS_PER_PROVIDER:
                    break
            if len(expanded_ror_queries) >= MAX_QUERY_EXPANSIONS_PER_PROVIDER:
                break

        deduped_ror = _dedupe_candidates(ror_candidates, key_fields=("id",))
        deduped_infoscience = _dedupe_candidates(
            infoscience_candidates,
            key_fields=("infoscienceOrgUnitIdentifier", "id"),
        )
        linked = _linked_candidates(deduped_ror, deduped_infoscience)

        return {
            "query": normalized_query,
            "ror_candidates": deduped_ror[:MAX_PROVIDER_CANDIDATES],
            "infoscience_candidates": deduped_infoscience[:MAX_PROVIDER_CANDIDATES],
            "linked_candidates": linked[:MAX_LINKED_CANDIDATES],
            "expanded_queries": {
                "ror": expanded_ror_queries,
                "infoscience": expanded_infoscience_queries,
            },
        }

    return Tool(
        search_organization_identity,
        name="search_organization_identity",
        description=(
            "Search ROR and Infoscience together and return provider candidates plus "
            "high-confidence linked matches based on normalized organization names."
        ),
    )
