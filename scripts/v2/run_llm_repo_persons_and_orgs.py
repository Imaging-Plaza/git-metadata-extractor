# ruff: noqa: INP001, T201
"""Run LLM repository + person + organization agents against a real GitHub repo.

Executes repository-oriented stages in order:
  1. context_gather
  2. repo_agent (LLM)
  3. person_agents fanout (LLM)
  4. organization_agents fanout (LLM)

Usage:
    just v2-run-repo-persons-and-orgs sdsc-ordes/gimie
    python scripts/v2/run_llm_repo_persons_and_orgs.py sdsc-ordes/gimie
    python scripts/v2/run_llm_repo_persons_and_orgs.py https://github.com/sdsc-ordes/gimie
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal

from src.v2.agents.llm.organization import LLMOrganizationAgentV2
from src.v2.agents.llm.person import LLMPersonAgentV2
from src.v2.agents.llm.repository import LLMRepositoryAgentV2
from src.v2.canonicalization.string_utils import normalize_string, strip_accents
from src.v2.dependencies import _default_provider_set
from src.v2.detection.github_url_classifier import classify_github_url
from src.v2.graph.export import JSONLDExporter
from src.v2.pipeline.stages import AssembledOutput, build_jsonld_output, gather_context

if TYPE_CHECKING:
    from src.v2.agents.models import AgentResult, ProviderSet

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_SEP = "─" * 60
_SEP_THIN = "·" * 60


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
    person_timeout_seconds: float,
    organization_timeout_seconds: float,
    max_concurrency: int,
    heartbeat_seconds: float,
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
    print("[ 1 / 4 ]  Gathering GIMIE context …\n")
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
    print("[ 2 / 4 ]  Running LLM repository agent …\n")

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

    # ── Stage 3: person_agents (concurrent) ─────────────────────────────────
    usernames = _contributor_usernames(repo_ctx)
    print(f"{_SEP}")
    print(f"[ 3 / 4 ]  Running LLM person agent for {len(usernames)} contributor(s): {usernames}\n")

    ordered_person_outcomes: list[_ExecutionOutcome] = []
    successful_person_entities: list[dict[str, object]] = []
    source_repositories = [full_name]

    if usernames:
        person_agent = LLMPersonAgentV2(
            llm_call_timeout_seconds=person_timeout_seconds,
        )
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _run_person(username: str) -> _ExecutionOutcome:
            async with semaphore:
                print(f"  → {username}: starting …")
                started_at = perf_counter()
                context = {
                    "username": username,
                    "source_url": url_info.normalized_url,
                    "source_repositories": source_repositories,
                    "repository_context": repo_ctx,
                }
                try:
                    result = await person_agent.run(context, providers)
                except Exception as exc:  # noqa: BLE001
                    elapsed_seconds = perf_counter() - started_at
                    status = _classify_error(exc)
                    print(f"  ✗ {username}: {status} in {elapsed_seconds:.1f}s ({exc})")
                    return _ExecutionOutcome(
                        subject=username,
                        status=status,
                        elapsed_seconds=elapsed_seconds,
                        error=str(exc),
                    )

                elapsed_seconds = perf_counter() - started_at
                print(f"  ✓ {username}: done in {elapsed_seconds:.1f}s")
                return _ExecutionOutcome(
                    subject=username,
                    status="ok",
                    elapsed_seconds=elapsed_seconds,
                    result=result,
                )

        person_outcomes_by_subject: dict[str, _ExecutionOutcome] = {}
        person_task_to_subject: dict[asyncio.Task[_ExecutionOutcome], str] = {
            asyncio.create_task(_run_person(username)): username for username in usernames
        }
        pending_person_tasks = set(person_task_to_subject)
        fanout_started_at = perf_counter()

        while pending_person_tasks:
            done, pending_person_tasks = await asyncio.wait(
                pending_person_tasks,
                timeout=heartbeat_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                running = sorted(person_task_to_subject[task] for task in pending_person_tasks)
                print(
                    "  … heartbeat: "
                    f"{len(running)} contributor(s) still running after {perf_counter() - fanout_started_at:.1f}s: "
                    f"{running}",
                )
                continue

            for task in done:
                subject = person_task_to_subject[task]
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
                person_outcomes_by_subject[subject] = outcome

        ordered_person_outcomes = [
            person_outcomes_by_subject[username] for username in usernames
        ]
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

        successful_person_entities = [
            outcome.result.data
            for outcome in ordered_person_outcomes
            if outcome.status == "ok" and outcome.result is not None
        ]
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
        "[ 4 / 4 ]  Running LLM organization agent for "
        f"{len(organization_names)} organization(s): {organization_names}\n",
    )

    ordered_organization_outcomes: list[_ExecutionOutcome] = []
    successful_organization_entities: list[dict[str, object]] = []
    if organization_names:
        organization_agent = LLMOrganizationAgentV2(
            llm_call_timeout_seconds=organization_timeout_seconds,
        )
        semaphore = asyncio.Semaphore(max_concurrency)
        upstream_outputs_for_org: dict[str, dict[str, object]] = {
            "repo_agent": repo_result.data,
        }
        for outcome in ordered_person_outcomes:
            if outcome.status == "ok" and outcome.result is not None:
                upstream_outputs_for_org[f"person_agent:{outcome.subject}"] = outcome.result.data

        upstream_outputs_json = json.dumps(
            upstream_outputs_for_org,
            ensure_ascii=True,
            sort_keys=True,
        )

        async def _run_org(org_name: str) -> _ExecutionOutcome:
            async with semaphore:
                print(f"  → {org_name}: starting …")
                started_at = perf_counter()
                context = {
                    "org_name": org_name,
                    "source_url": url_info.normalized_url,
                    "source_repositories": source_repositories,
                    "repository_context": repo_ctx,
                    "pipeline_outputs": upstream_outputs_for_org,
                    "upstream_stage_outputs_json": upstream_outputs_json,
                }
                try:
                    result = await organization_agent.run(context, providers)
                except Exception as exc:  # noqa: BLE001
                    elapsed_seconds = perf_counter() - started_at
                    status = _classify_error(exc)
                    print(f"  ✗ {org_name}: {status} in {elapsed_seconds:.1f}s ({exc})")
                    return _ExecutionOutcome(
                        subject=org_name,
                        status=status,
                        elapsed_seconds=elapsed_seconds,
                        error=str(exc),
                    )

                elapsed_seconds = perf_counter() - started_at
                print(f"  ✓ {org_name}: done in {elapsed_seconds:.1f}s")
                return _ExecutionOutcome(
                    subject=org_name,
                    status="ok",
                    elapsed_seconds=elapsed_seconds,
                    result=result,
                )

        organization_outcomes_by_subject: dict[str, _ExecutionOutcome] = {}
        organization_task_to_subject: dict[asyncio.Task[_ExecutionOutcome], str] = {
            asyncio.create_task(_run_org(org_name)): org_name for org_name in organization_names
        }
        pending_organization_tasks = set(organization_task_to_subject)
        fanout_started_at = perf_counter()

        while pending_organization_tasks:
            done, pending_organization_tasks = await asyncio.wait(
                pending_organization_tasks,
                timeout=heartbeat_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                running = sorted(
                    organization_task_to_subject[task]
                    for task in pending_organization_tasks
                )
                print(
                    "  … heartbeat: "
                    f"{len(running)} organization(s) still running after {perf_counter() - fanout_started_at:.1f}s: "
                    f"{running}",
                )
                continue

            for task in done:
                subject = organization_task_to_subject[task]
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
                organization_outcomes_by_subject[subject] = outcome

        ordered_organization_outcomes = [
            organization_outcomes_by_subject[org_name]
            for org_name in organization_names
        ]
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

        successful_organization_entities = [
            outcome.result.data
            for outcome in ordered_organization_outcomes
            if outcome.status == "ok" and outcome.result is not None
        ]
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

    combined_entities = _normalize_entities_for_debug_jsonld(
        [
            repo_result.data,
            *successful_person_entities,
            *successful_organization_entities,
        ],
    )
    if not combined_entities:
        combined_entities = [deepcopy(repo_result.data)]

    combined_jsonld = build_jsonld_output(
        assembled=AssembledOutput(
            root_entity=combined_entities[0],
            related_entities=combined_entities[1:],
        ),
        jsonld_context=JSONLDExporter().get_context()["@context"],
    )

    print(f"\n{_SEP}")
    print("Raw combined JSON-LD (pre-reconciliation):")
    print(_pj(combined_jsonld))
    print(_SEP)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run LLM repo + person + organization agents for repository mode stages.",
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
        "--max-concurrency",
        type=_positive_int,
        default=3,
        help="Maximum number of concurrent person/organization calls.",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=_positive_float,
        default=15.0,
        help="How often to print fanout heartbeat while waiting for tasks.",
    )
    args = parser.parse_args()
    asyncio.run(
        _run(
            args.repo,
            person_timeout_seconds=args.person_timeout_seconds,
            organization_timeout_seconds=args.organization_timeout_seconds,
            max_concurrency=args.max_concurrency,
            heartbeat_seconds=args.heartbeat_seconds,
        ),
    )


if __name__ == "__main__":
    main()
