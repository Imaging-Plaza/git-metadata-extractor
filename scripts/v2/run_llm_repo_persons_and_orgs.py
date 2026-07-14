# ruff: noqa: INP001
"""Run repository debug stages against a real GitHub repo.

Executes repository-oriented stages in order:
  1. context_gather
  2. repo_agent (LLM)
  3. person_agents fanout (LLM)
  4. organization_agents fanout (LLM)
  5. article_agents fanout (LLM)
  6. membership_agents fanout (LLM)
  7. contribution_agents fanout (LLM)

Usage:
    just v2-run-repo-persons-and-orgs sdsc-ordes/gimie
    just v2-run-repo-full-llm sdsc-ordes/gimie  # includes --verify-links
    python scripts/v2/run_llm_repo_persons_and_orgs.py sdsc-ordes/gimie
    python scripts/v2/run_llm_repo_persons_and_orgs.py https://github.com/sdsc-ordes/gimie
    python scripts/v2/run_llm_repo_persons_and_orgs.py sdsc-ordes/gimie --verify-links
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal
from urllib.parse import urlparse

from git_metadata_extractor.agents.llm.article import LLMArticleAgentV2
from git_metadata_extractor.agents.llm.contribution import LLMContributionAgentV2
from git_metadata_extractor.agents.llm.link_veracity import LLMLinkVeracityAgentV2
from git_metadata_extractor.agents.llm.membership import LLMMembershipAgentV2
from git_metadata_extractor.agents.llm.organization import LLMOrganizationAgentV2
from git_metadata_extractor.agents.llm.person import LLMPersonAgentV2
from git_metadata_extractor.agents.llm.repository import LLMRepositoryAgentV2
from git_metadata_extractor.agents.models import AgentResult, TypedEntityBuckets, infer_entity_bucket
from git_metadata_extractor.canonicalization.string_utils import normalize_string, strip_accents
from git_metadata_extractor.dependencies import _default_provider_set
from git_metadata_extractor.providers.detection.github_url_classifier import classify_github_url
from git_metadata_extractor.schema import load_jsonld_context
from git_metadata_extractor.pipeline.stages import (
    AssembledOutput,
    build_jsonld_output,
    gather_context,
    reconcile_entities,
)

if TYPE_CHECKING:
    from git_metadata_extractor.agents.models import ProviderSet

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_SEP = "─" * 60
_SEP_THIN = "·" * 60
_STAGE_REPO_AGENT = "repo_agent"
_STAGE_PERSON_AGENT = "person_agent"
_STAGE_ORG_AGENT = "org_agent"
_STAGE_ARTICLE_AGENT = "article_agent"
_STAGE_MEMBERSHIP_AGENT = "membership_agent"
_STAGE_CONTRIBUTION_AGENT = "contribution_agent"
_HTTP_SCHEMES = {"http", "https"}


@dataclass(slots=True)
class _ExecutionOutcome:
    subject: str
    status: Literal["ok", "timeout", "error"]
    elapsed_seconds: float
    result: AgentResult | None = None
    error: str | None = None


def _pj(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _dedupe_preserve_order_normalized(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_string(value)
        token = normalized if normalized else value.casefold().strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(value)
    return deduped


def _lookup_token_variants(token: str) -> list[str]:
    candidate = token.strip()
    if not candidate:
        return []

    variants: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        normalized_value = value.strip()
        if not normalized_value or normalized_value in seen:
            return
        seen.add(normalized_value)
        variants.append(normalized_value)

    raw_candidates = [candidate]
    if candidate.startswith("@"):
        raw_candidates.append(candidate[1:])

    for raw_value in raw_candidates:
        lowered = raw_value.casefold().strip()
        _add(lowered)
        _add(strip_accents(lowered).strip())
        collapsed = normalize_string(raw_value)
        _add(collapsed)
        _add(collapsed.replace(" ", ""))

    return variants


def _register_lookup_token(lookup: dict[str, str], token: Any, canonical_id: str) -> None:
    if not isinstance(token, str):
        return
    for normalized in _lookup_token_variants(token):
        lookup[normalized] = canonical_id


def _resolve_lookup_token(lookup: dict[str, str], token: Any) -> str | None:
    if not isinstance(token, str):
        return None
    for normalized in _lookup_token_variants(token):
        resolved = lookup.get(normalized)
        if isinstance(resolved, str):
            return resolved
    return None


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _json_fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    except TypeError:
        return repr(value)


def _merge_lists(existing: list[Any], incoming: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        fingerprint = _json_fingerprint(item)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        merged.append(deepcopy(item))
    return merged


def _merge_values(existing: Any, incoming: Any) -> Any:
    if incoming is None:
        return deepcopy(existing)
    if existing is None:
        return deepcopy(incoming)
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = deepcopy(existing)
        for key, value in incoming.items():
            if key not in merged:
                merged[key] = deepcopy(value)
                continue
            merged[key] = _merge_values(merged[key], value)
        return merged
    if isinstance(existing, list) and isinstance(incoming, list):
        return _merge_lists(existing, incoming)
    if existing == incoming:
        return deepcopy(existing)
    return deepcopy(existing)


def _merge_entities_by_id(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged_entities: list[dict[str, Any]] = []
    index_by_id: dict[str, int] = {}

    for entity in entities:
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or not entity_id:
            merged_entities.append(deepcopy(entity))
            continue

        existing_index = index_by_id.get(entity_id)
        if existing_index is None:
            index_by_id[entity_id] = len(merged_entities)
            merged_entities.append(deepcopy(entity))
            continue

        merged_entities[existing_index] = _merge_values(
            merged_entities[existing_index],
            entity,
        )

    return merged_entities


def _build_person_lookup(entities: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if entity.get("type") != "schema:Person":
            continue
        canonical_id = entity.get("id")
        if not isinstance(canonical_id, str) or not canonical_id:
            continue
        _register_lookup_token(lookup, canonical_id, canonical_id)
        for key in (
            "pulse:githubUsername",
            "pulse:orcidIdentifier",
            "pulse:infosciencePersonIdentifier",
            "schema:name",
        ):
            _register_lookup_token(lookup, entity.get(key), canonical_id)

        schema_url = entity.get("schema:url")
        if isinstance(schema_url, str) and "github.com/" in schema_url:
            github_handle = schema_url.rsplit("/", maxsplit=1)[-1]
            _register_lookup_token(lookup, github_handle, canonical_id)

        identifiers = entity.get("identifiers")
        if isinstance(identifiers, dict):
            for key in (
                "pulse:orcid",
                "pulse:infosciencePersonIdentifier",
                "pulse:githubUsername",
                "uuid",
            ):
                _register_lookup_token(lookup, identifiers.get(key), canonical_id)
    return lookup


def _build_organization_lookup(entities: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if entity.get("type") != "org:Organization":
            continue
        canonical_id = entity.get("id")
        if not isinstance(canonical_id, str) or not canonical_id:
            continue
        _register_lookup_token(lookup, canonical_id, canonical_id)
        for key in (
            "schema:name",
            "pulse:githubOrganizationHandle",
            "pulse:ror",
            "schema:identifier",
            "pulse:infoscienceOrganizationIdentifier",
        ):
            _register_lookup_token(lookup, entity.get(key), canonical_id)

        for key in ("aliases", "acronyms"):
            for alias in _as_string_list(entity.get(key)):
                _register_lookup_token(lookup, alias, canonical_id)

        identifiers = entity.get("identifiers")
        if isinstance(identifiers, dict):
            for key in (
                "pulse:ror",
                "pulse:infoscienceOrganizationIdentifier",
                "pulse:githubOrganizationHandle",
                "uuid",
            ):
                _register_lookup_token(lookup, identifiers.get(key), canonical_id)
    return lookup


def _build_repository_lookup(entities: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if entity.get("type") != "schema:SoftwareSourceCode":
            continue
        canonical_id = entity.get("id")
        if not isinstance(canonical_id, str) or not canonical_id:
            continue
        _register_lookup_token(lookup, canonical_id, canonical_id)
        for key in ("pulse:githubRepositoryHandle", "schema:citation"):
            _register_lookup_token(lookup, entity.get(key), canonical_id)

        identifiers = entity.get("identifiers")
        if isinstance(identifiers, dict):
            _register_lookup_token(
                lookup,
                identifiers.get("pulse:githubRepositoryHandle"),
                canonical_id,
            )
            _register_lookup_token(
                lookup,
                identifiers.get("schema:citation"),
                canonical_id,
            )
    return lookup


def _map_reference_list(
    values: list[str],
    lookup: dict[str, str],
    *,
    drop_if_matches_lookup: dict[str, str] | None = None,
) -> list[str]:
    mapped: list[str] = []
    for value in values:
        resolved = _resolve_lookup_token(lookup, value)
        if resolved is not None:
            mapped.append(resolved)
            continue
        if drop_if_matches_lookup is not None and _resolve_lookup_token(
            drop_if_matches_lookup,
            value,
        ) is not None:
            continue
        mapped.append(value)
    return _dedupe_preserve_order(mapped)


def _backfill_organization_identifier(entity: dict[str, Any]) -> None:
    schema_identifier = entity.get("schema:identifier")
    if isinstance(schema_identifier, str) and schema_identifier:
        return

    ror_candidates: list[str] = []
    pulse_ror = entity.get("pulse:ror")
    if isinstance(pulse_ror, str) and pulse_ror:
        ror_candidates.append(pulse_ror)

    identifiers = entity.get("identifiers")
    if isinstance(identifiers, dict):
        identifier_ror = identifiers.get("pulse:ror")
        if isinstance(identifier_ror, str) and identifier_ror:
            ror_candidates.append(identifier_ror)

    entity_id = entity.get("id")
    if isinstance(entity_id, str) and entity_id.startswith("https://ror.org/"):
        ror_candidates.append(entity_id)

    if ror_candidates:
        entity["schema:identifier"] = ror_candidates[0]


def _strip_none_values(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if item is None:
                continue
            cleaned_item = _strip_none_values(item)
            if cleaned_item is None:
                continue
            if isinstance(cleaned_item, (dict, list)) and not cleaned_item:
                continue
            cleaned[key] = cleaned_item
        return cleaned
    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            cleaned_item = _strip_none_values(item)
            if cleaned_item is None:
                continue
            if isinstance(cleaned_item, (dict, list)) and not cleaned_item:
                continue
            cleaned_list.append(cleaned_item)
        return cleaned_list
    return value


def _normalize_entities_for_debug_jsonld(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged_entities = _merge_entities_by_id(entities)

    person_lookup = _build_person_lookup(merged_entities)
    organization_lookup = _build_organization_lookup(merged_entities)
    repository_lookup = _build_repository_lookup(merged_entities)

    for entity in merged_entities:
        if entity.get("type") == "schema:SoftwareSourceCode":
            authors = _as_string_list(entity.get("schema:author"))
            entity["schema:author"] = _map_reference_list(
                authors,
                person_lookup,
                drop_if_matches_lookup=organization_lookup,
            )

            owned_by = entity.get("pulse:ownedBy")
            if isinstance(owned_by, str):
                resolved_owner = _resolve_lookup_token(person_lookup, owned_by) or _resolve_lookup_token(
                    organization_lookup,
                    owned_by,
                )
                if isinstance(resolved_owner, str):
                    entity["pulse:ownedBy"] = resolved_owner

            fork_ref = entity.get("pulse:isForkOf")
            if isinstance(fork_ref, str):
                resolved_fork = _resolve_lookup_token(repository_lookup, fork_ref)
                if isinstance(resolved_fork, str):
                    entity["pulse:isForkOf"] = resolved_fork

        if entity.get("type") == "schema:Person":
            owns = _as_string_list(entity.get("pulse:owns"))
            entity["pulse:owns"] = _map_reference_list(owns, repository_lookup)

        if entity.get("type") == "org:Organization":
            _backfill_organization_identifier(entity)

            unit_of = entity.get("org:unitOf")
            if isinstance(unit_of, str):
                resolved_unit_of = _resolve_lookup_token(organization_lookup, unit_of)
                if isinstance(resolved_unit_of, str):
                    entity["org:unitOf"] = resolved_unit_of

            has_units = _as_string_list(entity.get("org:hasUnit"))
            entity["org:hasUnit"] = _map_reference_list(has_units, organization_lookup)

            owns = _as_string_list(entity.get("pulse:owns"))
            entity["pulse:owns"] = _map_reference_list(owns, repository_lookup)

    normalized_entities = [_strip_none_values(entity) for entity in merged_entities]
    return [
        entity
        for entity in normalized_entities
        if isinstance(entity, dict)
    ]


def _contributor_usernames(repository_context: dict) -> list[str]:
    """Extract unique contributor logins, excluding organization accounts."""
    contributors = repository_context.get("contributors", [])
    seen: set[str] = set()
    logins: list[str] = []
    for contributor in contributors:
        if isinstance(contributor, dict):
            if str(contributor.get("type", "")).lower() == "organization":
                continue
            login = contributor.get("login")
        elif isinstance(contributor, str):
            login = contributor
        else:
            continue
        if isinstance(login, str) and login.strip() and login not in seen:
            seen.add(login)
            logins.append(login.strip())
    return logins


def _organization_candidates(
    repository_context: dict[str, object],
    person_outcomes: list[_ExecutionOutcome],
) -> list[str]:
    candidates: list[str] = []
    metadata = repository_context.get("metadata")
    if isinstance(metadata, dict):
        owner = metadata.get("owner")
        if isinstance(owner, dict):
            owner_login = owner.get("login")
            owner_type = owner.get("type")
            if (
                isinstance(owner_login, str)
                and owner_login
                and isinstance(owner_type, str)
                and owner_type.lower() == "organization"
            ):
                candidates.append(owner_login)

    for outcome in person_outcomes:
        if outcome.status != "ok" or outcome.result is None:
            continue
        memberships = outcome.result.data.get("org:hasMembership")
        if not isinstance(memberships, list):
            continue
        for membership in memberships:
            if not isinstance(membership, str) or "_" not in membership:
                continue
            _, organization = membership.split("_", maxsplit=1)
            organization = organization.strip()
            if organization:
                candidates.append(organization)

    return _dedupe_preserve_order_normalized(candidates)


def _collect_stage_payloads(
    pipeline_outputs: dict[str, dict[str, Any]],
    stage_prefix: str,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for result_key in sorted(pipeline_outputs):
        if not str(result_key).startswith(stage_prefix):
            continue
        payload = pipeline_outputs[result_key]
        if isinstance(payload, dict) and payload:
            payloads.append(deepcopy(payload))
    return payloads


def _collect_stage_derivations(
    pipeline_agent_results: dict[str, AgentResult],
    stage_prefix: str,
) -> list[dict[str, Any]]:
    derivations: list[dict[str, Any]] = []
    for result_key in sorted(pipeline_agent_results):
        if not str(result_key).startswith(stage_prefix):
            continue
        result = pipeline_agent_results[result_key]
        if not isinstance(result, AgentResult):
            continue
        if not isinstance(result.stats, dict):
            continue
        derivation = result.stats.get("derivation")
        if isinstance(derivation, dict):
            derivations.append(deepcopy(derivation))
    return derivations


def _typed_entity_bucket_snapshot(
    pipeline_outputs: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    buckets = TypedEntityBuckets()
    for result_key in sorted(pipeline_outputs):
        payload = pipeline_outputs[result_key]
        if not isinstance(payload, dict) or not payload:
            continue
        bucket_name = infer_entity_bucket(agent_key=str(result_key), data=payload)
        if bucket_name is None:
            continue
        buckets.add(bucket_name, payload)
    return buckets.to_dict()


def _class_agent_base_context(
    *,
    source_url: str,
    full_name: str,
    repository_context: dict[str, Any],
    pipeline_outputs: dict[str, dict[str, Any]],
    pipeline_agent_results: dict[str, AgentResult],
) -> dict[str, Any]:
    return {
        "detected_type": "repository",
        "source_url": source_url,
        "full_name": full_name,
        "repository_context": deepcopy(repository_context),
        "known_persons": _collect_stage_payloads(
            pipeline_outputs,
            _STAGE_PERSON_AGENT,
        ),
        "known_organizations": _collect_stage_payloads(
            pipeline_outputs,
            _STAGE_ORG_AGENT,
        ),
        "known_repositories": _collect_stage_payloads(
            pipeline_outputs,
            _STAGE_REPO_AGENT,
        ),
        "person_derivations": _collect_stage_derivations(
            pipeline_agent_results,
            _STAGE_PERSON_AGENT,
        ),
        "organization_derivations": _collect_stage_derivations(
            pipeline_agent_results,
            _STAGE_ORG_AGENT,
        ),
        "repository_derivations": _collect_stage_derivations(
            pipeline_agent_results,
            _STAGE_REPO_AGENT,
        ),
        "typed_entity_buckets": _typed_entity_bucket_snapshot(pipeline_outputs),
        "pipeline_outputs": deepcopy(pipeline_outputs),
        "upstream_stage_outputs_json": json.dumps(
            pipeline_outputs,
            ensure_ascii=True,
            sort_keys=True,
        ),
    }


def _person_derivations_by_id(
    pipeline_agent_results: dict[str, AgentResult],
) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for derivation in _collect_stage_derivations(
        pipeline_agent_results,
        _STAGE_PERSON_AGENT,
    ):
        person_id = derivation.get("person_id")
        if isinstance(person_id, str) and person_id:
            by_id[person_id] = derivation
    return by_id


def _repository_derivations_by_id(
    pipeline_agent_results: dict[str, AgentResult],
) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for derivation in _collect_stage_derivations(
        pipeline_agent_results,
        _STAGE_REPO_AGENT,
    ):
        repository_id = derivation.get("repository_full_name")
        if isinstance(repository_id, str) and repository_id:
            by_id[repository_id] = derivation
    return by_id


def _merge_person_with_derivation(
    person: dict[str, Any],
    derivation: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = deepcopy(person)
    if not isinstance(derivation, dict):
        return merged

    affiliation_names = derivation.get("affiliation_names")
    if isinstance(affiliation_names, list):
        merged["affiliations"] = [
            value
            for value in affiliation_names
            if isinstance(value, str) and value
        ]

    orcid_affiliations = derivation.get("orcid_affiliations")
    if isinstance(orcid_affiliations, list):
        merged["orcid_affiliations"] = [
            deepcopy(value)
            for value in orcid_affiliations
            if isinstance(value, dict)
        ]

    source_repositories = derivation.get("source_repositories")
    if isinstance(source_repositories, list):
        merged["source_repositories"] = [
            value
            for value in source_repositories
            if isinstance(value, str) and value
        ]

    return merged


def _merge_repository_with_derivation(
    repository: dict[str, Any],
    derivation: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = deepcopy(repository)
    if not isinstance(derivation, dict):
        return merged

    contributors = derivation.get("contributors")
    if isinstance(contributors, list):
        merged["contributors"] = [
            deepcopy(contributor)
            for contributor in contributors
            if isinstance(contributor, (dict, str))
        ]
    return merged


def _article_fanout_contexts_repository(
    *,
    base_context: dict[str, Any],
    full_name: str,
) -> list[dict[str, Any]]:
    seeds = _dedupe_preserve_order([full_name] if full_name else [])
    contexts: list[dict[str, Any]] = []
    for seed in seeds:
        context = deepcopy(base_context)
        context["article_seed"] = seed
        context["full_name"] = seed
        contexts.append(context)
    return contexts


def _membership_fanout_contexts_repository(
    *,
    base_context: dict[str, Any],
    pipeline_agent_results: dict[str, AgentResult],
) -> list[dict[str, Any]]:
    known_persons = base_context.get("known_persons")
    known_organizations = base_context.get("known_organizations")
    if not isinstance(known_persons, list) or not isinstance(known_organizations, list):
        return []
    if not known_persons or not known_organizations:
        return []

    person_derivations = _person_derivations_by_id(pipeline_agent_results)
    contexts: list[dict[str, Any]] = []
    seen_person_ids: set[str] = set()

    for person in sorted(
        [item for item in known_persons if isinstance(item, dict)],
        key=lambda item: str(item.get("id", "")),
    ):
        person_id = person.get("id")
        if not isinstance(person_id, str) or not person_id or person_id in seen_person_ids:
            continue
        seen_person_ids.add(person_id)

        merged_person = _merge_person_with_derivation(
            person,
            person_derivations.get(person_id),
        )
        context = deepcopy(base_context)
        context["membership_seed"] = person_id
        context["known_persons"] = [merged_person]
        context["known_organizations"] = deepcopy(known_organizations)
        contexts.append(context)

    return contexts


def _contribution_fanout_contexts_repository(
    *,
    base_context: dict[str, Any],
    pipeline_agent_results: dict[str, AgentResult],
) -> list[dict[str, Any]]:
    known_persons = base_context.get("known_persons")
    known_repositories = base_context.get("known_repositories")
    if not isinstance(known_persons, list) or not isinstance(known_repositories, list):
        return []
    if not known_persons or not known_repositories:
        return []

    repository_derivations = _repository_derivations_by_id(pipeline_agent_results)
    contexts: list[dict[str, Any]] = []
    seen_repository_ids: set[str] = set()

    for repository in sorted(
        [item for item in known_repositories if isinstance(item, dict)],
        key=lambda item: str(
            item.get("id")
            or item.get("pulse:githubRepositoryHandle")
            or item.get("full_name")
            or "",
        ),
    ):
        repository_id = repository.get("id")
        if not isinstance(repository_id, str) or not repository_id or repository_id in seen_repository_ids:
            continue
        seen_repository_ids.add(repository_id)

        merged_repository = _merge_repository_with_derivation(
            repository,
            repository_derivations.get(repository_id),
        )
        context = deepcopy(base_context)
        context["contribution_seed"] = repository_id
        context["known_persons"] = deepcopy(known_persons)
        context["known_repositories"] = [merged_repository]
        contexts.append(context)

    return contexts


def _append_pipeline_output(
    pipeline_outputs: dict[str, dict[str, Any]],
    pipeline_agent_results: dict[str, AgentResult],
    *,
    result_key: str,
    result: AgentResult,
) -> None:
    pipeline_outputs[result_key] = deepcopy(result.data)
    pipeline_agent_results[result_key] = result


def _extract_result_entities(
    result: AgentResult,
    *,
    stats_keys: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    if isinstance(result.data, dict) and result.data:
        entities.append(deepcopy(result.data))
    if not isinstance(result.stats, dict):
        return entities

    for stats_key in stats_keys:
        values = result.stats.get(stats_key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict) and value:
                entities.append(deepcopy(value))
    return entities


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in _HTTP_SCHEMES and bool(parsed.netloc)


def _collect_unique_http_link_contexts(
    jsonld_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    graph = jsonld_payload.get("@graph")
    if not isinstance(graph, list):
        return []

    link_contexts: dict[str, dict[str, Any]] = {}

    def _record_link(
        *,
        link: str,
        source_entity_id: str | None,
        predicate: str | None,
    ) -> None:
        normalized_link = link.strip()
        if not normalized_link or not _is_http_url(normalized_link):
            return

        context = link_contexts.setdefault(
            normalized_link,
            {
                "link": normalized_link,
                "source_entity_id": source_entity_id,
                "predicate": predicate,
                "relationships": [],
            },
        )
        relationship = {
            "source_entity_id": source_entity_id,
            "predicate": predicate,
        }
        if relationship not in context["relationships"]:
            context["relationships"].append(relationship)

    def _walk(
        value: Any,
        *,
        source_entity_id: str | None,
        predicate: str | None,
    ) -> None:
        if isinstance(value, str):
            _record_link(
                link=value,
                source_entity_id=source_entity_id,
                predicate=predicate,
            )
            return

        if isinstance(value, dict):
            at_id = value.get("@id")
            if isinstance(at_id, str):
                _record_link(
                    link=at_id,
                    source_entity_id=source_entity_id,
                    predicate=predicate,
                )
            for key, nested in value.items():
                if key == "@id":
                    continue
                next_predicate = predicate if predicate else key
                _walk(
                    nested,
                    source_entity_id=source_entity_id,
                    predicate=next_predicate,
                )
            return

        if isinstance(value, list):
            for nested in value:
                _walk(
                    nested,
                    source_entity_id=source_entity_id,
                    predicate=predicate,
                )

    for node in graph:
        if not isinstance(node, dict):
            continue
        source_entity_id = node.get("@id") if isinstance(node.get("@id"), str) else None
        for key, value in node.items():
            if key == "@id":
                continue
            _walk(
                value,
                source_entity_id=source_entity_id,
                predicate=key,
            )

    return [link_contexts[link] for link in sorted(link_contexts)]


async def _run_fanout_stage(
    *,
    subjects: list[str],
    running_label: str,
    max_concurrency: int,
    heartbeat_seconds: float,
    run_subject: Callable[[str], Awaitable[AgentResult]],
) -> list[_ExecutionOutcome]:
    if not subjects:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _run_one(subject: str) -> _ExecutionOutcome:
        async with semaphore:
            print(f"  → {subject}: starting …")
            started_at = perf_counter()
            try:
                result = await run_subject(subject)
            except Exception as exc:  # noqa: BLE001
                elapsed_seconds = perf_counter() - started_at
                status = _classify_error(exc)
                print(f"  ✗ {subject}: {status} in {elapsed_seconds:.1f}s ({exc})")
                return _ExecutionOutcome(
                    subject=subject,
                    status=status,
                    elapsed_seconds=elapsed_seconds,
                    error=str(exc),
                )

            elapsed_seconds = perf_counter() - started_at
            print(f"  ✓ {subject}: done in {elapsed_seconds:.1f}s")
            return _ExecutionOutcome(
                subject=subject,
                status="ok",
                elapsed_seconds=elapsed_seconds,
                result=result,
            )

    outcomes_by_subject: dict[str, _ExecutionOutcome] = {}
    task_to_subject: dict[asyncio.Task[_ExecutionOutcome], str] = {
        asyncio.create_task(_run_one(subject)): subject for subject in subjects
    }
    pending_tasks = set(task_to_subject)
    fanout_started_at = perf_counter()

    while pending_tasks:
        done, pending_tasks = await asyncio.wait(
            pending_tasks,
            timeout=heartbeat_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            running = sorted(task_to_subject[task] for task in pending_tasks)
            print(
                "  … heartbeat: "
                f"{len(running)} {running_label}(s) still running after {perf_counter() - fanout_started_at:.1f}s: "
                f"{running}",
            )
            continue

        for task in done:
            subject = task_to_subject[task]
            try:
                outcome = task.result()
            except Exception as exc:  # noqa: BLE001
                status = _classify_error(exc)
                outcome = _ExecutionOutcome(
                    subject=subject,
                    status=status,
                    elapsed_seconds=perf_counter() - fanout_started_at,
                    error=str(exc),
                )
            outcomes_by_subject[subject] = outcome

    return [outcomes_by_subject[subject] for subject in subjects]


def _classify_error(error: BaseException) -> Literal["timeout", "error"]:
    if "timed out" in str(error).lower():
        return "timeout"
    return "error"


def _positive_float(raw_value: str) -> float:
    parsed = float(raw_value)
    if parsed <= 0:
        message = "value must be > 0"
        raise argparse.ArgumentTypeError(message)
    return parsed


def _positive_int(raw_value: str) -> int:
    parsed = int(raw_value)
    if parsed <= 0:
        message = "value must be > 0"
        raise argparse.ArgumentTypeError(message)
    return parsed


async def _run(  # noqa: C901, PLR0915
    repo: str,
    *,
    person_timeout_seconds: float = 180.0,
    organization_timeout_seconds: float = 180.0,
    article_timeout_seconds: float = 180.0,
    membership_timeout_seconds: float = 180.0,
    contribution_timeout_seconds: float = 180.0,
    max_concurrency: int = 3,
    heartbeat_seconds: float = 15.0,
    verify_links: bool = False,
    link_verification_timeout_seconds: float = 120.0,
    link_verification_max_concurrency: int | None = None,
) -> None:
    if "/" in repo and not repo.startswith("http") and "github.com" not in repo:
        repo = f"github.com/{repo}"
    url_info = classify_github_url(repo)
    full_name = f"{url_info.owner}/{url_info.repo}"

    print(f"\n{_SEP}")
    print(f"  Repository : {full_name}")
    print(f"{_SEP}\n")

    providers: ProviderSet = _default_provider_set(use_mock_providers=False)

    # ── Stage 1: context_gather ──────────────────────────────────────────────
    print("[ 1 / 7 ]  Gathering GIMIE context …\n")
    bundle = await gather_context("repository", url_info, providers)
    repo_ctx = bundle.context.get("repository", {})

    readme = repo_ctx.get("readme_content") or ""
    print(f"Metadata:\n{_pj(repo_ctx.get('metadata', {}))}\n")
    print(f"Contributors:\n{_pj(repo_ctx.get('contributors', []))}\n")
    print(f"Languages:\n{_pj(repo_ctx.get('languages', {}))}\n")
    print(f"README ({len(readme)} chars): {readme[:300]}{'…' if len(readme) > 300 else ''}\n")
    if bundle.warnings:
        print(f"Context warnings: {bundle.warnings}\n")

    # ── Stage 2: repo_agent ──────────────────────────────────────────────────
    print(f"{_SEP}")
    print("[ 2 / 7 ]  Running LLM repository agent …\n")

    repo_agent = LLMRepositoryAgentV2()
    repo_result = await repo_agent.run(
        {
            "full_name": full_name,
            "source_url": url_info.normalized_url,
            "repository_context": repo_ctx,
        },
        providers,
    )

    print(f"Model    : {repo_result.model}")
    print(f"Provider : {repo_result.provider}")
    print(f"Tokens   : prompt={repo_result.tokens_prompt}  completion={repo_result.tokens_completion}")
    if repo_result.warnings:
        print("Warnings:")
        for warning in repo_result.warnings:
            print(f"  • {warning}")
    print(f"\nRepository entity:\n{_pj(repo_result.data)}\n")

    pipeline_outputs: dict[str, dict[str, Any]] = {
        _STAGE_REPO_AGENT: deepcopy(repo_result.data),
    }
    pipeline_agent_results: dict[str, AgentResult] = {
        _STAGE_REPO_AGENT: repo_result,
    }

    # ── Stage 3: person_agents (concurrent) ─────────────────────────────────
    usernames = _contributor_usernames(repo_ctx)
    print(f"{_SEP}")
    print(f"[ 3 / 7 ]  Running LLM person agent for {len(usernames)} contributor(s): {usernames}\n")

    ordered_person_outcomes: list[_ExecutionOutcome] = []
    successful_person_entities: list[dict[str, Any]] = []
    source_repositories = [full_name]

    if usernames:
        person_agent = LLMPersonAgentV2(
            llm_call_timeout_seconds=person_timeout_seconds,
        )
        person_context_by_username: dict[str, dict[str, Any]] = {
            username: {
                "username": username,
                "source_url": url_info.normalized_url,
                "source_repositories": source_repositories,
                "repository_context": repo_ctx,
            }
            for username in usernames
        }

        async def _run_person(username: str) -> AgentResult:
            return await person_agent.run(person_context_by_username[username], providers)

        ordered_person_outcomes = await _run_fanout_stage(
            subjects=usernames,
            running_label="contributor",
            max_concurrency=max_concurrency,
            heartbeat_seconds=heartbeat_seconds,
            run_subject=_run_person,
        )
        for outcome in ordered_person_outcomes:
            print(_SEP_THIN)
            if outcome.status != "ok" or outcome.result is None:
                print(f"  Person: {outcome.subject}")
                print(f"  Status   : {outcome.status}")
                print(f"  Error    : {outcome.error}\n")
                continue
            result = outcome.result
            print(f"  Person: {outcome.subject}")
            print(f"  Model    : {result.model}")
            print(f"  Tokens   : prompt={result.tokens_prompt}  completion={result.tokens_completion}")
            if result.warnings:
                print("  Warnings:")
                for warning in result.warnings:
                    print(f"    • {warning}")
            print(f"  Entity:\n{_pj(result.data)}\n")

        for outcome in ordered_person_outcomes:
            if outcome.status != "ok" or outcome.result is None:
                continue
            _append_pipeline_output(
                pipeline_outputs,
                pipeline_agent_results,
                result_key=f"{_STAGE_PERSON_AGENT}:{outcome.subject}",
                result=outcome.result,
            )
            successful_person_entities.extend(_extract_result_entities(outcome.result))

        timeout_count = sum(1 for outcome in ordered_person_outcomes if outcome.status == "timeout")
        error_count = sum(1 for outcome in ordered_person_outcomes if outcome.status == "error")

        print(_SEP_THIN)
        print(
            "Person summary: "
            f"ok={len(successful_person_entities)} timeout={timeout_count} error={error_count}",
        )
        failed_person_outcomes = [
            outcome for outcome in ordered_person_outcomes if outcome.status != "ok"
        ]
        if failed_person_outcomes:
            print("Failed contributors:")
            for outcome in failed_person_outcomes:
                print(f"  - {outcome.subject}: {outcome.status} ({outcome.error})")
        print("\nPerson entities:")
        print(_pj(successful_person_entities))
    else:
        print("  No contributors found — skipping person stage.\n")

    # ── Stage 4: organization_agents (concurrent) ───────────────────────────
    organization_names = _organization_candidates(repo_ctx, ordered_person_outcomes)
    print(f"{_SEP}")
    print(
        "[ 4 / 7 ]  Running LLM organization agent for "
        f"{len(organization_names)} organization(s): {organization_names}\n",
    )

    ordered_organization_outcomes: list[_ExecutionOutcome] = []
    successful_organization_entities: list[dict[str, Any]] = []
    if organization_names:
        organization_agent = LLMOrganizationAgentV2(
            llm_call_timeout_seconds=organization_timeout_seconds,
        )
        upstream_outputs_for_org = deepcopy(pipeline_outputs)

        upstream_outputs_json = json.dumps(
            upstream_outputs_for_org,
            ensure_ascii=True,
            sort_keys=True,
        )

        organization_context_by_name: dict[str, dict[str, Any]] = {
            org_name: {
                "org_name": org_name,
                "source_url": url_info.normalized_url,
                "source_repositories": source_repositories,
                "repository_context": repo_ctx,
                "pipeline_outputs": deepcopy(upstream_outputs_for_org),
                "upstream_stage_outputs_json": upstream_outputs_json,
            }
            for org_name in organization_names
        }

        async def _run_org(org_name: str) -> AgentResult:
            return await organization_agent.run(organization_context_by_name[org_name], providers)

        ordered_organization_outcomes = await _run_fanout_stage(
            subjects=organization_names,
            running_label="organization",
            max_concurrency=max_concurrency,
            heartbeat_seconds=heartbeat_seconds,
            run_subject=_run_org,
        )
        for outcome in ordered_organization_outcomes:
            print(_SEP_THIN)
            if outcome.status != "ok" or outcome.result is None:
                print(f"  Organization: {outcome.subject}")
                print(f"  Status   : {outcome.status}")
                print(f"  Error    : {outcome.error}\n")
                continue
            result = outcome.result
            print(f"  Organization: {outcome.subject}")
            print(f"  Model    : {result.model}")
            print(f"  Tokens   : prompt={result.tokens_prompt}  completion={result.tokens_completion}")
            if result.warnings:
                print("  Warnings:")
                for warning in result.warnings:
                    print(f"    • {warning}")
            print(f"  Entity:\n{_pj(result.data)}\n")

        for outcome in ordered_organization_outcomes:
            if outcome.status != "ok" or outcome.result is None:
                continue
            _append_pipeline_output(
                pipeline_outputs,
                pipeline_agent_results,
                result_key=f"{_STAGE_ORG_AGENT}:{outcome.subject}",
                result=outcome.result,
            )
            successful_organization_entities.extend(
                _extract_result_entities(outcome.result),
            )

        timeout_count = sum(
            1 for outcome in ordered_organization_outcomes if outcome.status == "timeout"
        )
        error_count = sum(
            1 for outcome in ordered_organization_outcomes if outcome.status == "error"
        )

        print(_SEP_THIN)
        print(
            "Organization summary: "
            f"ok={len(successful_organization_entities)} timeout={timeout_count} error={error_count}",
        )
        failed_org_outcomes = [
            outcome for outcome in ordered_organization_outcomes if outcome.status != "ok"
        ]
        if failed_org_outcomes:
            print("Failed organizations:")
            for outcome in failed_org_outcomes:
                print(f"  - {outcome.subject}: {outcome.status} ({outcome.error})")
        print("\nOrganization entities:")
        print(_pj(successful_organization_entities))
    else:
        print("  No organizations derived — skipping organization stage.\n")

    # ── Stage 5: article_agents (concurrent) ────────────────────────────────
    article_base_context = _class_agent_base_context(
        source_url=url_info.normalized_url,
        full_name=full_name,
        repository_context=repo_ctx,
        pipeline_outputs=pipeline_outputs,
        pipeline_agent_results=pipeline_agent_results,
    )
    article_contexts = _article_fanout_contexts_repository(
        base_context=article_base_context,
        full_name=full_name,
    )
    article_seeds = [context["article_seed"] for context in article_contexts]

    print(f"{_SEP}")
    print(
        "[ 5 / 7 ]  Running LLM article agent for "
        f"{len(article_seeds)} seed(s): {article_seeds}\n",
    )

    ordered_article_outcomes: list[_ExecutionOutcome] = []
    successful_article_entities: list[dict[str, Any]] = []
    if article_contexts:
        article_agent = LLMArticleAgentV2(
            llm_call_timeout_seconds=article_timeout_seconds,
        )
        article_context_by_seed: dict[str, dict[str, Any]] = {
            context["article_seed"]: context for context in article_contexts
        }

        async def _run_article(seed: str) -> AgentResult:
            return await article_agent.run(article_context_by_seed[seed], providers)

        ordered_article_outcomes = await _run_fanout_stage(
            subjects=article_seeds,
            running_label="article",
            max_concurrency=max_concurrency,
            heartbeat_seconds=heartbeat_seconds,
            run_subject=_run_article,
        )

        for outcome in ordered_article_outcomes:
            print(_SEP_THIN)
            if outcome.status != "ok" or outcome.result is None:
                print(f"  Article: {outcome.subject}")
                print(f"  Status   : {outcome.status}")
                print(f"  Error    : {outcome.error}\n")
                continue
            result = outcome.result
            print(f"  Article: {outcome.subject}")
            print(f"  Model    : {result.model}")
            print(f"  Tokens   : prompt={result.tokens_prompt}  completion={result.tokens_completion}")
            if result.warnings:
                print("  Warnings:")
                for warning in result.warnings:
                    print(f"    • {warning}")
            print(f"  Entity:\n{_pj(result.data)}\n")

        for outcome in ordered_article_outcomes:
            if outcome.status != "ok" or outcome.result is None:
                continue
            _append_pipeline_output(
                pipeline_outputs,
                pipeline_agent_results,
                result_key=f"{_STAGE_ARTICLE_AGENT}:{outcome.subject}",
                result=outcome.result,
            )
            successful_article_entities.extend(
                _extract_result_entities(
                    outcome.result,
                    stats_keys=("articles",),
                ),
            )

        timeout_count = sum(1 for outcome in ordered_article_outcomes if outcome.status == "timeout")
        error_count = sum(1 for outcome in ordered_article_outcomes if outcome.status == "error")
        print(_SEP_THIN)
        print(
            "Article summary: "
            f"ok={len(successful_article_entities)} timeout={timeout_count} error={error_count}",
        )
        failed_article_outcomes = [
            outcome for outcome in ordered_article_outcomes if outcome.status != "ok"
        ]
        if failed_article_outcomes:
            print("Failed article seeds:")
            for outcome in failed_article_outcomes:
                print(f"  - {outcome.subject}: {outcome.status} ({outcome.error})")
        print("\nArticle entities:")
        print(_pj(successful_article_entities))
    else:
        print("  No article seeds derived — skipping article stage.\n")

    # ── Stage 6: membership_agents (concurrent) ─────────────────────────────
    membership_base_context = _class_agent_base_context(
        source_url=url_info.normalized_url,
        full_name=full_name,
        repository_context=repo_ctx,
        pipeline_outputs=pipeline_outputs,
        pipeline_agent_results=pipeline_agent_results,
    )
    membership_contexts = _membership_fanout_contexts_repository(
        base_context=membership_base_context,
        pipeline_agent_results=pipeline_agent_results,
    )
    membership_seeds = [context["membership_seed"] for context in membership_contexts]

    print(f"{_SEP}")
    print(
        "[ 6 / 7 ]  Running LLM membership agent for "
        f"{len(membership_seeds)} seed(s): {membership_seeds}\n",
    )

    ordered_membership_outcomes: list[_ExecutionOutcome] = []
    successful_membership_entities: list[dict[str, Any]] = []
    if membership_contexts:
        membership_agent = LLMMembershipAgentV2(
            llm_call_timeout_seconds=membership_timeout_seconds,
        )
        membership_context_by_seed: dict[str, dict[str, Any]] = {
            context["membership_seed"]: context for context in membership_contexts
        }

        async def _run_membership(seed: str) -> AgentResult:
            return await membership_agent.run(membership_context_by_seed[seed], providers)

        ordered_membership_outcomes = await _run_fanout_stage(
            subjects=membership_seeds,
            running_label="membership",
            max_concurrency=max_concurrency,
            heartbeat_seconds=heartbeat_seconds,
            run_subject=_run_membership,
        )

        for outcome in ordered_membership_outcomes:
            print(_SEP_THIN)
            if outcome.status != "ok" or outcome.result is None:
                print(f"  Membership: {outcome.subject}")
                print(f"  Status   : {outcome.status}")
                print(f"  Error    : {outcome.error}\n")
                continue
            result = outcome.result
            print(f"  Membership: {outcome.subject}")
            print(f"  Model    : {result.model}")
            print(f"  Tokens   : prompt={result.tokens_prompt}  completion={result.tokens_completion}")
            if result.warnings:
                print("  Warnings:")
                for warning in result.warnings:
                    print(f"    • {warning}")
            print(f"  Entity:\n{_pj(result.data)}\n")

        for outcome in ordered_membership_outcomes:
            if outcome.status != "ok" or outcome.result is None:
                continue
            _append_pipeline_output(
                pipeline_outputs,
                pipeline_agent_results,
                result_key=f"{_STAGE_MEMBERSHIP_AGENT}:{outcome.subject}",
                result=outcome.result,
            )
            successful_membership_entities.extend(
                _extract_result_entities(
                    outcome.result,
                    stats_keys=("memberships",),
                ),
            )

        timeout_count = sum(1 for outcome in ordered_membership_outcomes if outcome.status == "timeout")
        error_count = sum(1 for outcome in ordered_membership_outcomes if outcome.status == "error")
        print(_SEP_THIN)
        print(
            "Membership summary: "
            f"ok={len(successful_membership_entities)} timeout={timeout_count} error={error_count}",
        )
        failed_membership_outcomes = [
            outcome for outcome in ordered_membership_outcomes if outcome.status != "ok"
        ]
        if failed_membership_outcomes:
            print("Failed membership seeds:")
            for outcome in failed_membership_outcomes:
                print(f"  - {outcome.subject}: {outcome.status} ({outcome.error})")
        print("\nMembership entities:")
        print(_pj(successful_membership_entities))
    else:
        print("  No membership seeds derived — skipping membership stage.\n")

    # ── Stage 7: contribution_agents (concurrent) ───────────────────────────
    contribution_base_context = _class_agent_base_context(
        source_url=url_info.normalized_url,
        full_name=full_name,
        repository_context=repo_ctx,
        pipeline_outputs=pipeline_outputs,
        pipeline_agent_results=pipeline_agent_results,
    )
    contribution_contexts = _contribution_fanout_contexts_repository(
        base_context=contribution_base_context,
        pipeline_agent_results=pipeline_agent_results,
    )
    contribution_seeds = [context["contribution_seed"] for context in contribution_contexts]

    print(f"{_SEP}")
    print(
        "[ 7 / 7 ]  Running LLM contribution agent for "
        f"{len(contribution_seeds)} seed(s): {contribution_seeds}\n",
    )

    ordered_contribution_outcomes: list[_ExecutionOutcome] = []
    successful_contribution_entities: list[dict[str, Any]] = []
    if contribution_contexts:
        contribution_agent = LLMContributionAgentV2(
            llm_call_timeout_seconds=contribution_timeout_seconds,
        )
        contribution_context_by_seed: dict[str, dict[str, Any]] = {
            context["contribution_seed"]: context for context in contribution_contexts
        }

        async def _run_contribution(seed: str) -> AgentResult:
            return await contribution_agent.run(
                contribution_context_by_seed[seed],
                providers,
            )

        ordered_contribution_outcomes = await _run_fanout_stage(
            subjects=contribution_seeds,
            running_label="contribution",
            max_concurrency=max_concurrency,
            heartbeat_seconds=heartbeat_seconds,
            run_subject=_run_contribution,
        )

        for outcome in ordered_contribution_outcomes:
            print(_SEP_THIN)
            if outcome.status != "ok" or outcome.result is None:
                print(f"  Contribution: {outcome.subject}")
                print(f"  Status   : {outcome.status}")
                print(f"  Error    : {outcome.error}\n")
                continue
            result = outcome.result
            print(f"  Contribution: {outcome.subject}")
            print(f"  Model    : {result.model}")
            print(f"  Tokens   : prompt={result.tokens_prompt}  completion={result.tokens_completion}")
            if result.warnings:
                print("  Warnings:")
                for warning in result.warnings:
                    print(f"    • {warning}")
            print(f"  Entity:\n{_pj(result.data)}\n")

        for outcome in ordered_contribution_outcomes:
            if outcome.status != "ok" or outcome.result is None:
                continue
            _append_pipeline_output(
                pipeline_outputs,
                pipeline_agent_results,
                result_key=f"{_STAGE_CONTRIBUTION_AGENT}:{outcome.subject}",
                result=outcome.result,
            )
            successful_contribution_entities.extend(
                _extract_result_entities(
                    outcome.result,
                    stats_keys=("contributions",),
                ),
            )

        timeout_count = sum(
            1 for outcome in ordered_contribution_outcomes if outcome.status == "timeout"
        )
        error_count = sum(
            1 for outcome in ordered_contribution_outcomes if outcome.status == "error"
        )
        print(_SEP_THIN)
        print(
            "Contribution summary: "
            f"ok={len(successful_contribution_entities)} timeout={timeout_count} error={error_count}",
        )
        failed_contribution_outcomes = [
            outcome for outcome in ordered_contribution_outcomes if outcome.status != "ok"
        ]
        if failed_contribution_outcomes:
            print("Failed contribution seeds:")
            for outcome in failed_contribution_outcomes:
                print(f"  - {outcome.subject}: {outcome.status} ({outcome.error})")
        print("\nContribution entities:")
        print(_pj(successful_contribution_entities))
    else:
        print("  No contribution seeds derived — skipping contribution stage.\n")

    combined_entities = _normalize_entities_for_debug_jsonld(
        [
            repo_result.data,
            *successful_person_entities,
            *successful_organization_entities,
            *successful_article_entities,
            *successful_membership_entities,
            *successful_contribution_entities,
        ],
    )
    if not combined_entities:
        combined_entities = [deepcopy(repo_result.data)]

    combined_jsonld = build_jsonld_output(
        assembled=AssembledOutput(
            root_entity=combined_entities[0],
            related_entities=combined_entities[1:],
        ),
        jsonld_context=load_jsonld_context(),
    )

    print(f"\n{_SEP}")
    print("Raw combined JSON-LD (pre-reconciliation):")
    print(_pj(combined_jsonld))
    print(_SEP)

    # --- Reconciliation pass ---------------------------------------------------
    entities_by_type: dict[str, list[dict[str, Any]]] = {
        "repositories": [deepcopy(repo_result.data)],
        "persons": [deepcopy(e) for e in successful_person_entities],
        "organizations": [deepcopy(e) for e in successful_organization_entities],
        "articles": [deepcopy(e) for e in successful_article_entities],
        "memberships": [deepcopy(e) for e in successful_membership_entities],
        "contributions": [deepcopy(e) for e in successful_contribution_entities],
    }
    reconciled = reconcile_entities(entities_by_type)

    reconciled_all: list[dict[str, Any]] = []
    for entity_list in reconciled.entities.values():
        reconciled_all.extend(entity_list)
    reconciled_all.extend(reconciled.memberships)
    reconciled_all.extend(reconciled.contributions)
    reconciled_all = _merge_entities_by_id(reconciled_all)

    if reconciled_all:
        reconciled_jsonld = build_jsonld_output(
            assembled=AssembledOutput(
                root_entity=reconciled_all[0],
                related_entities=reconciled_all[1:],
            ),
            jsonld_context=load_jsonld_context(),
        )
        print(f"\n{_SEP}")
        print("Reconciled JSON-LD (post-reconciliation):")
        print(_pj(reconciled_jsonld))
        if reconciled.link_warnings:
            print("\nLink warnings:")
            for warning in reconciled.link_warnings:
                print(f"  - {warning}")
        print(_SEP)

    if not verify_links:
        return

    link_contexts = _collect_unique_http_link_contexts(combined_jsonld)
    print(f"{_SEP}")
    print(
        "[ links ]  Verifying unique http(s) links via LLM + Selenium tool for "
        f"{len(link_contexts)} link(s)",
    )

    if not link_contexts:
        print("  No http(s) links found in combined JSON-LD graph.")
        print(_SEP)
        return

    verifier = LLMLinkVeracityAgentV2(
        llm_call_timeout_seconds=link_verification_timeout_seconds,
    )
    verifier_context_by_link = {
        context["link"]: {
            **context,
            "source_url": url_info.normalized_url,
        }
        for context in link_contexts
        if isinstance(context.get("link"), str)
    }
    links = sorted(verifier_context_by_link)
    verification_concurrency = (
        link_verification_max_concurrency
        if isinstance(link_verification_max_concurrency, int)
        and link_verification_max_concurrency > 0
        else max_concurrency
    )

    async def _run_link_verification(link: str) -> AgentResult:
        return await verifier.run(verifier_context_by_link[link], providers)

    verification_outcomes = await _run_fanout_stage(
        subjects=links,
        running_label="link verification",
        max_concurrency=verification_concurrency,
        heartbeat_seconds=heartbeat_seconds,
        run_subject=_run_link_verification,
    )

    verification_yes = 0
    verification_no = 0
    for outcome in verification_outcomes:
        print(_SEP_THIN)
        if outcome.status != "ok" or outcome.result is None:
            verification_no += 1
            print(f"  Link   : {outcome.subject}")
            print(f"  Verdict: no (execution {outcome.status})")
            print(f"  Error  : {outcome.error}")
            continue

        payload = outcome.result.data
        verdict = bool(payload.get("relationship_supported"))
        if verdict:
            verification_yes += 1
        else:
            verification_no += 1
        print(f"  Link   : {outcome.subject}")
        print(f"  Verdict: {'yes' if verdict else 'no'}")
        summary = payload.get("relationship_summary")
        if isinstance(summary, str) and summary:
            print(f"  Why    : {summary}")

    timeout_count = sum(1 for outcome in verification_outcomes if outcome.status == "timeout")
    error_count = sum(1 for outcome in verification_outcomes if outcome.status == "error")
    print(_SEP_THIN)
    print(
        "Link verification summary: "
        f"yes={verification_yes} no={verification_no} timeout={timeout_count} error={error_count}",
    )
    print(_SEP)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run repository debug stages (repo/person/org/article/membership/contribution).",
    )
    parser.add_argument("repo", help="GitHub repo as owner/repo or full URL")
    parser.add_argument(
        "--person-timeout-seconds",
        type=_positive_float,
        default=180.0,
        help="Per-person timeout for LLM person extraction (seconds).",
    )
    parser.add_argument(
        "--organization-timeout-seconds",
        type=_positive_float,
        default=180.0,
        help="Per-organization timeout for LLM organization extraction (seconds).",
    )
    parser.add_argument(
        "--article-timeout-seconds",
        type=_positive_float,
        default=180.0,
        help="Per-article timeout for LLM article extraction (seconds).",
    )
    parser.add_argument(
        "--membership-timeout-seconds",
        type=_positive_float,
        default=180.0,
        help="Per-membership timeout for LLM membership extraction (seconds).",
    )
    parser.add_argument(
        "--contribution-timeout-seconds",
        type=_positive_float,
        default=180.0,
        help="Per-contribution timeout for LLM contribution extraction (seconds).",
    )
    parser.add_argument(
        "--max-concurrency",
        type=_positive_int,
        default=3,
        help="Maximum number of concurrent fanout agent calls.",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=_positive_float,
        default=15.0,
        help="How often to print fanout heartbeat while waiting for tasks.",
    )
    parser.add_argument(
        "--verify-links",
        action="store_true",
        help=(
            "Run independent link-veracity checks over unique http(s) links in the final "
            "combined JSON-LD using Selenium-backed content retrieval."
        ),
    )
    parser.add_argument(
        "--link-verification-timeout-seconds",
        type=_positive_float,
        default=120.0,
        help="Per-link timeout for LLM link veracity checks (seconds).",
    )
    parser.add_argument(
        "--link-verification-max-concurrency",
        type=_positive_int,
        default=3,
        help="Maximum number of concurrent link-verification agent calls.",
    )
    args = parser.parse_args()
    asyncio.run(
        _run(
            args.repo,
            person_timeout_seconds=args.person_timeout_seconds,
            organization_timeout_seconds=args.organization_timeout_seconds,
            article_timeout_seconds=args.article_timeout_seconds,
            membership_timeout_seconds=args.membership_timeout_seconds,
            contribution_timeout_seconds=args.contribution_timeout_seconds,
            max_concurrency=args.max_concurrency,
            heartbeat_seconds=args.heartbeat_seconds,
            verify_links=args.verify_links,
            link_verification_timeout_seconds=args.link_verification_timeout_seconds,
            link_verification_max_concurrency=args.link_verification_max_concurrency,
        ),
    )


if __name__ == "__main__":
    main()
