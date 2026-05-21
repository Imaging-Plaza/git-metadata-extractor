"""Hybrid-runtime LLM refinement stage.

Runs after `reconcile_entities`. For each Organization, Repository, and Person
in the reconciled graph, calls a typed LLM "refiner" that may propose a small
patch (whitelisted fields only). The whitelist is enforced here — fields
outside it are dropped and a warning is logged.

Determinístic fields (counts, stars/forks, identifiers, dates) are never
exposed to the refiner contract via the whitelist, so the LLM cannot
accidentally clobber them even when its model emits them.

Opt-in via ``agent_runtime=hybrid``. Each entity runs through its refiner
independently with bounded concurrency.
"""

from __future__ import annotations

import asyncio
import logging
import os
from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import re

from src.v2.agents.llm.agent_tools.graph_neighbors import make_get_entity_neighbors_tool
from src.v2.agents.llm.refiners import (
    MembershipRefinerAgent,
    MembershipRefinerInput,
    OrganizationRefinerAgent,
    OrganizationRefinerInput,
    PersonRefinerAgent,
    PersonRefinerInput,
    RepositoryRefinerAgent,
    RepositoryRefinerInput,
)
from src.v2.agents.llm.refiners.discovery import (
    DiscoveredArticle,
    DiscoveredOrg,
    DiscoveredPerson,
    DiscoveryRefinerAgent,
    DiscoveryRefinerInput,
)
from src.v2.agents.llm.refiners.rescue import (
    RescueCandidate,
    RescueRefinerAgent,
    RescueRefinerInput,
)
from src.v2.agents.llm.runtime import LLMRuntimeError
from src.v2.api_models.enums import OrganizationTypeV2
from src.v2.ingest.providers.epfl_graph_rag import EpflGraphRagProvider
from src.v2.pipeline.stages.models import ReconciledEntities

logger = logging.getLogger(__name__)

ORG_PATCHABLE_FIELDS: frozenset[str] = frozenset({"pulse:OrganizationType"})
REPO_PATCHABLE_FIELDS: frozenset[str] = frozenset(
    {"pulse:discipline", "pulse:repositoryType"},
)
PERSON_PATCHABLE_FIELDS: frozenset[str] = frozenset({"schema:name"})
MEMBERSHIP_PATCHABLE_FIELDS: frozenset[str] = frozenset({"org:role"})

README_CONTEXT_MAX_CHARS = 1500
DEFAULT_MAX_CONCURRENCY = 4

# Discovery refiner thresholds. The LLM is told to only propose
# entities with confidence >= 0.7 (see prompt); we re-check that floor
# defensively because the validator can't trust the model alone.
DISCOVERY_CONFIDENCE_FLOOR = 0.7
DISCOVERY_README_CAP = 8000
DISCOVERY_CITATION_CAP = 4000
DISCOVERY_REPLY_MAX_PER_TYPE = 10   # safety cap on additions per extract
_ORCID_RE = re.compile(r"\b(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b")
_VALID_ORG_TYPES: frozenset[str] = frozenset(t.value for t in OrganizationTypeV2)

EPFL_GRAPH_HITS_TOP_K = 8
EPFL_GRAPH_HIT_FIELDS: frozenset[str] = frozenset(
    {"category_id", "name", "depth", "parent_id", "wikipedia_url", "score"},
)
EPFL_GRAPH_MIN_SCORE_DEFAULT = 0.85
EPFL_GRAPH_MIN_SCORE_ENV = "V2_EPFL_GRAPH_MIN_SCORE"


def _epfl_graph_min_score() -> float:
    raw = os.getenv(EPFL_GRAPH_MIN_SCORE_ENV)
    if raw is None or not raw.strip():
        return EPFL_GRAPH_MIN_SCORE_DEFAULT
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "refine_with_llm: %s=%r is not a float — falling back to %.2f",
            EPFL_GRAPH_MIN_SCORE_ENV,
            raw,
            EPFL_GRAPH_MIN_SCORE_DEFAULT,
        )
        return EPFL_GRAPH_MIN_SCORE_DEFAULT


@dataclass(slots=True)
class RefineWithLLMResult:
    """Outcome of the refinement stage across all entity types."""

    reconciled: ReconciledEntities
    warnings: list[str] = field(default_factory=list)
    refined_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    by_type: dict[str, dict[str, int]] = field(default_factory=dict)


def is_enabled() -> bool:
    """Return True when the hybrid refiner stage is enabled (default true)."""

    raw = os.getenv("V2_HYBRID_REFINER_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


def _build_repo_context_summary(
    *,
    gathered_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(gathered_context, dict):
        return {}
    repository_context = gathered_context.get("repository")
    if not isinstance(repository_context, dict):
        return {}

    summary: dict[str, Any] = {}
    metadata = repository_context.get("metadata")
    if isinstance(metadata, dict):
        meta_summary = {
            key: metadata.get(key)
            for key in ("name", "full_name", "description", "owner")
            if metadata.get(key) is not None
        }
        if meta_summary:
            summary["metadata"] = meta_summary

    readme = repository_context.get("readme_content")
    if isinstance(readme, str) and readme:
        summary["readme_excerpt"] = readme[:README_CONTEXT_MAX_CHARS]

    return summary


async def _fetch_epfl_graph_hits(
    *,
    provider: EpflGraphRagProvider | None,
    repo_context_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run one EPFL Graph disciplines RAG search using README + description.

    Returns a thin list of `{category_id, name, depth, parent_id, wikipedia_url, score}`
    dicts (top-K) that the repository refiner uses to ground its discipline choice.
    Returns `[]` on any error / missing input — never raises.
    """

    if provider is None:
        return []

    metadata = repo_context_summary.get("metadata") or {}
    parts: list[str] = []
    name = metadata.get("name")
    description = metadata.get("description")
    readme = repo_context_summary.get("readme_excerpt")
    for value in (name, description, readme):
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    query = "\n\n".join(parts).strip()
    if not query:
        return []

    try:
        hits = await provider.search(query, top_k=EPFL_GRAPH_HITS_TOP_K, rerank=True)
    except Exception as exc:  # noqa: BLE001 — RAG availability is optional
        logger.warning("refine_with_llm: EPFL Graph RAG search failed — %s", exc)
        return []

    return [
        {key: hit.get(key) for key in EPFL_GRAPH_HIT_FIELDS if key in hit}
        for hit in hits
    ]


def _filter_hits_by_score(
    hits: list[dict[str, Any]],
    *,
    min_score: float,
) -> list[dict[str, Any]]:
    """Keep only hits with a numeric ``score >= min_score``. Stable order."""

    if not hits:
        return []
    filtered: list[dict[str, Any]] = []
    for hit in hits:
        score = hit.get("score")
        if isinstance(score, (int, float)) and float(score) >= min_score:
            filtered.append(hit)
    return filtered


def _apply_patch(
    *,
    entity: dict[str, Any],
    patch: dict[str, Any],
    whitelist: frozenset[str],
    type_label: str,
) -> tuple[bool, list[str]]:
    """Merge `patch` into `entity` honouring the whitelist."""

    warnings: list[str] = []
    changed = False
    for key, value in patch.items():
        if key not in whitelist:
            warnings.append(
                f"refine_with_llm: dropped non-whitelisted field '{key}' "
                f"({type_label}={entity.get('schema:name')!r})",
            )
            continue
        current = entity.get(key)
        if current == value:
            continue
        entity[key] = value
        changed = True
    return changed, warnings


async def _run_refiner_safely(
    *,
    coro: Any,
    entity: dict[str, Any],
    type_label: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Wrap a refiner call so a single failure cannot kill the stage."""

    name = entity.get("schema:name") or entity.get("id", "?")
    try:
        patch = await coro
        return entity, patch, []
    except LLMRuntimeError as exc:
        warning = f"refine_with_llm: {type_label} refiner failed for {name!r}: {exc}"
        logger.warning(warning)
        return entity, {}, [warning]
    except Exception as exc:  # noqa: BLE001 — boundary for stage isolation
        warning = (
            f"refine_with_llm: unexpected error in {type_label} refiner for "
            f"{name!r}: {exc}"
        )
        logger.exception(warning)
        return entity, {}, [warning]


async def _refine_org(
    *,
    organization: dict[str, Any],
    refiner: OrganizationRefinerAgent,
    repo_context_summary: dict[str, Any],
    neighbors_tool: Any,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    async with semaphore:
        return await _run_refiner_safely(
            coro=refiner.run(
                refiner_input=OrganizationRefinerInput(
                    entity=deepcopy(organization),
                    repo_context_summary=repo_context_summary,
                ),
                tools=[neighbors_tool],
            ),
            entity=organization,
            type_label="organization",
        )


async def _refine_repo(
    *,
    repository: dict[str, Any],
    refiner: RepositoryRefinerAgent,
    repo_context_summary: dict[str, Any],
    neighbors_tool: Any,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    async with semaphore:
        return await _run_refiner_safely(
            coro=refiner.run(
                refiner_input=RepositoryRefinerInput(
                    entity=deepcopy(repository),
                    repo_context_summary=repo_context_summary,
                ),
                tools=[neighbors_tool],
            ),
            entity=repository,
            type_label="repository",
        )


async def _refine_person(
    *,
    person: dict[str, Any],
    refiner: PersonRefinerAgent,
    repo_context_summary: dict[str, Any],
    neighbors_tool: Any,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    async with semaphore:
        return await _run_refiner_safely(
            coro=refiner.run(
                refiner_input=PersonRefinerInput(
                    entity=deepcopy(person),
                    repo_context_summary=repo_context_summary,
                ),
                tools=[neighbors_tool],
            ),
            entity=person,
            type_label="person",
        )


async def _refine_membership(
    *,
    membership: dict[str, Any],
    refiner: MembershipRefinerAgent,
    organization_name: str | None,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    async with semaphore:
        return await _run_refiner_safely(
            coro=refiner.run(
                refiner_input=MembershipRefinerInput(
                    entity=deepcopy(membership),
                    organization_name=organization_name,
                ),
            ),
            entity=membership,
            type_label="membership",
        )


def _resolve_org_name_for_membership(
    *,
    membership: dict[str, Any],
    org_index: dict[str, str],
) -> str | None:
    org_ref = membership.get("org:organization")
    if isinstance(org_ref, dict):
        org_ref = org_ref.get("@id") or org_ref.get("id")
    if isinstance(org_ref, str):
        return org_index.get(org_ref)
    return None


def _gate_repo_type_patch(
    *,
    repository: dict[str, Any],
    patch: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Drop `pulse:repositoryType` from the patch when the current value is not `pulse:Other`.

    The prompt already tells the model to skip; this is a server-side safety net.
    """

    if "pulse:repositoryType" not in patch:
        return patch, []
    current = repository.get("pulse:repositoryType")
    if current == "pulse:Other":
        return patch, []
    pruned = {k: v for k, v in patch.items() if k != "pulse:repositoryType"}
    warning = (
        "refine_with_llm: dropped repository type rewrite "
        f"({repository.get('schema:name')!r}: current={current} → proposed={patch['pulse:repositoryType']}) — "
        "only `pulse:Other` may be reclassified"
    )
    return pruned, [warning]


# ---------------------------------------------------------------------------
# Discovery refiner: additive entities (Persons, Orgs, Articles)
# ---------------------------------------------------------------------------


def _normalize_orcid(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.startswith("https://orcid.org/"):
        candidate = candidate[len("https://orcid.org/"):]
    candidate = candidate.upper()
    if _ORCID_RE.fullmatch(candidate):
        return candidate
    return None


def _normalize_github_handle(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    handle = value.strip().lstrip("@")
    # Accept full URL form too.
    if handle.startswith("https://github.com/"):
        handle = handle[len("https://github.com/"):].strip("/")
    handle = handle.split("/", maxsplit=1)[0]
    if not handle or " " in handle:
        return None
    return handle


def _normalize_ror(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate.startswith("https://ror.org/"):
        return None
    return candidate


def _normalize_doi_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.startswith("https://doi.org/"):
        return candidate
    if candidate.startswith("10."):
        return f"https://doi.org/{candidate}"
    return None


def _materialize_person(
    proposal: DiscoveredPerson,
    existing_ids: set[str],
) -> tuple[dict[str, Any] | None, str | None]:
    """Convert a DiscoveredPerson into a SHACL-compliant Person entity.

    Returns (entity_dict, warning_or_none). When the proposal can't be
    materialised (missing identifier, duplicate id, low confidence), the
    entity is None and the warning explains why.
    """
    if proposal.confidence < DISCOVERY_CONFIDENCE_FLOOR:
        return None, (
            f"discovery_refiner: dropped Person proposal {proposal.schema_name!r} "
            f"(confidence={proposal.confidence:.2f} < {DISCOVERY_CONFIDENCE_FLOOR})"
        )
    github_handle = _normalize_github_handle(proposal.pulse_githubUsername)
    orcid = _normalize_orcid(proposal.pulse_orcidIdentifier)
    if not github_handle and not orcid:
        return None, (
            f"discovery_refiner: dropped Person proposal {proposal.schema_name!r} "
            "(no github handle nor ORCID identifier)"
        )
    if orcid:
        person_id = f"https://orcid.org/{orcid}"
        id_source = "pulse:orcid"
    else:
        person_id = f"https://github.com/{github_handle}"
        id_source = "pulse:githubUsername"
    if person_id in existing_ids:
        return None, (
            f"discovery_refiner: dropped Person proposal {proposal.schema_name!r} "
            f"(@id {person_id} already in graph)"
        )
    return (
        {
            "id": person_id,
            "type": "schema:Person",
            "shacl": "pulse:PersonShape",
            "identifiers": {
                "pulse:orcid": f"https://orcid.org/{orcid}" if orcid else None,
                "pulse:githubUsername": github_handle,
            },
            "idSource": id_source,
            "schema:name": proposal.schema_name,
            "pulse:githubUsername": github_handle,
            "pulse:orcidIdentifier": f"https://orcid.org/{orcid}" if orcid else None,
            "schema:email": proposal.schema_email or None,
            "_source": "hybrid_refiner",
            "_discovery_reason": proposal.reason[:240] if proposal.reason else "",
            "_discovery_confidence": proposal.confidence,
        },
        None,
    )


def _materialize_org(
    proposal: DiscoveredOrg,
    existing_ids: set[str],
) -> tuple[dict[str, Any] | None, str | None]:
    if proposal.confidence < DISCOVERY_CONFIDENCE_FLOOR:
        return None, (
            f"discovery_refiner: dropped Org proposal {proposal.schema_name!r} "
            f"(confidence={proposal.confidence:.2f} < {DISCOVERY_CONFIDENCE_FLOOR})"
        )
    ror = _normalize_ror(proposal.pulse_ror)
    gh_handle = _normalize_github_handle(proposal.pulse_githubOrganizationHandle)
    if not ror and not gh_handle:
        return None, (
            f"discovery_refiner: dropped Org proposal {proposal.schema_name!r} "
            "(no ROR nor github handle)"
        )
    org_type = (
        proposal.pulse_OrganizationType
        if proposal.pulse_OrganizationType in _VALID_ORG_TYPES
        else "pulse:OtherOrganizationType"
    )
    if ror:
        org_id = ror
        id_source = "pulse:ror"
    else:
        org_id = f"https://github.com/{gh_handle}"
        id_source = "pulse:githubOrganizationHandle"
    if org_id in existing_ids:
        return None, (
            f"discovery_refiner: dropped Org proposal {proposal.schema_name!r} "
            f"(@id {org_id} already in graph)"
        )
    return (
        {
            "id": org_id,
            "type": "org:Organization",
            "shacl": "pulse:OrganizationShape",
            "identifiers": {
                "pulse:ror": ror,
                "pulse:githubOrganizationHandle": gh_handle,
            },
            "idSource": id_source,
            "schema:name": proposal.schema_name,
            "schema:identifier": ror,
            "pulse:githubOrganizationHandle": gh_handle,
            "pulse:OrganizationType": org_type,
            "_source": "hybrid_refiner",
            "_discovery_reason": proposal.reason[:240] if proposal.reason else "",
            "_discovery_confidence": proposal.confidence,
        },
        None,
    )


def _materialize_article(
    proposal: DiscoveredArticle,
    existing_ids: set[str],
) -> tuple[dict[str, Any] | None, str | None]:
    if proposal.confidence < DISCOVERY_CONFIDENCE_FLOOR:
        return None, (
            f"discovery_refiner: dropped Article proposal {proposal.schema_name!r} "
            f"(confidence={proposal.confidence:.2f} < {DISCOVERY_CONFIDENCE_FLOOR})"
        )
    doi_url = _normalize_doi_url(proposal.schema_identifier)
    if not doi_url:
        return None, (
            f"discovery_refiner: dropped Article proposal {proposal.schema_name!r} "
            f"(missing/malformed DOI: {proposal.schema_identifier!r})"
        )
    if doi_url in existing_ids:
        return None, (
            f"discovery_refiner: dropped Article proposal {proposal.schema_name!r} "
            f"(DOI {doi_url} already in graph)"
        )
    return (
        {
            "id": doi_url,
            "type": "schema:ScholarlyArticle",
            "shacl": "pulse:ArticleShape",
            "identifiers": {"schema:identifier": doi_url},
            "idSource": "schema:identifier",
            "schema:name": proposal.schema_name,
            "schema:identifier": doi_url,
            "schema:datePublished": proposal.schema_datePublished,
            "_source": "hybrid_refiner",
            "_discovery_reason": proposal.reason[:240] if proposal.reason else "",
            "_discovery_confidence": proposal.confidence,
            "_proposed_author_names": list(proposal.author_names or []),
        },
        None,
    )


async def _run_rescue_pass(
    *,
    reconciled: ReconciledEntities,
    gathered_context: dict[str, Any] | None,
) -> tuple[list[str], dict[str, int]]:
    """LLM-judged rescue of evidence-thin Memberships that the
    deterministic reconciliation pass dropped.

    Pulls `_dropped_affiliations` (stamped by `_normalize_membership_entities`)
    off each Person, presents the list to the LLM together with the
    repo context, and re-instantiates the Memberships the model
    explicitly approves with a verbatim-quote justification. Synthesises
    a minimal Org stub when the rescued link's target is not yet in
    the graph.
    """
    stats = {"candidates": 0, "rescued": 0, "rejected": 0}

    persons = reconciled.entities.get("persons") or []
    organizations = reconciled.entities.get("organizations") or []

    persons_by_id = {p["id"]: p for p in persons if isinstance(p.get("id"), str)}
    org_ids = {o["id"] for o in organizations if isinstance(o.get("id"), str)}

    candidates: list[RescueCandidate] = []
    for person in persons:
        dropped = person.get("_dropped_affiliations")
        if not isinstance(dropped, list):
            continue
        person_id = person.get("id")
        person_name = person.get("schema:name")
        for entry in dropped:
            if not isinstance(entry, dict):
                continue
            membership_id = entry.get("membership_id")
            if not isinstance(membership_id, str):
                continue
            candidates.append(
                RescueCandidate(
                    membership_id=membership_id,
                    person_id=person_id or "",
                    person_name=person_name,
                    org_id=entry.get("org_id"),
                    org_name=entry.get("org_name"),
                    reason=entry.get("reason"),
                ),
            )
    stats["candidates"] = len(candidates)

    if not candidates:
        return ([], stats)

    repo_handle = ""
    readme_text: str | None = None
    citation_cff: str | None = None
    aux_files: dict[str, str] = {}
    if isinstance(gathered_context, dict):
        repo_ctx = gathered_context.get("repository") or {}
        if isinstance(repo_ctx, dict):
            repo_handle = repo_ctx.get("full_name") or ""
            readme_value = repo_ctx.get("readme_content")
            if isinstance(readme_value, str) and readme_value.strip():
                readme_text = readme_value
            # Pull every aux file we collected (AUTHORS, NOTICE.yml,
            # pyproject.toml, …). If CITATION.cff lives in aux_files,
            # mirror it into the dedicated slot too for prompt clarity.
            aux_value = repo_ctx.get("aux_files")
            if isinstance(aux_value, dict):
                aux_files = {
                    k: v for k, v in aux_value.items()
                    if isinstance(v, str) and v.strip()
                }
            cff_from_aux = aux_files.get("CITATION.cff") or aux_files.get("citation.cff")
            if isinstance(cff_from_aux, str) and cff_from_aux.strip():
                citation_cff = cff_from_aux
            else:
                metadata = repo_ctx.get("metadata") or {}
                if isinstance(metadata, dict):
                    cff = metadata.get("citation_cff") or metadata.get("CITATION_cff")
                    if isinstance(cff, str) and cff.strip():
                        citation_cff = cff

    if not readme_text and not citation_cff and not aux_files:
        return ([], stats)

    refiner = RescueRefinerAgent()
    refiner_input = RescueRefinerInput(
        repo_handle=repo_handle,
        readme_text=readme_text,
        citation_cff=citation_cff,
        aux_files=aux_files,
        candidates=candidates,
        existing_org_ids=sorted(org_ids),
    )
    warnings: list[str] = []
    try:
        output = await refiner.run(refiner_input=refiner_input)
    except LLMRuntimeError as exc:
        warnings.append(f"rescue_refiner: skipped — {exc}")
        return (warnings, stats)
    except Exception as exc:  # noqa: BLE001
        logger.exception("rescue_refiner crashed")
        warnings.append(f"rescue_refiner: skipped (unexpected error) — {exc}")
        return (warnings, stats)

    DISCOVERY_FLOOR = 0.7  # noqa: N806 — local constant
    # Index candidates by membership_id for quick lookup on decisions.
    candidate_index = {c.membership_id: c for c in candidates}
    new_orgs: list[dict[str, Any]] = []
    new_memberships: list[dict[str, Any]] = []
    accepted_membership_ids: set[str] = set()

    for decision in output.decisions:
        candidate = candidate_index.get(decision.membership_id)
        if candidate is None:
            warnings.append(
                f"rescue_refiner: dropped decision for unknown membership "
                f"{decision.membership_id!r}",
            )
            continue
        if decision.confidence < DISCOVERY_FLOOR:
            warnings.append(
                f"rescue_refiner: dropped rescue of {candidate.membership_id!r} "
                f"(confidence={decision.confidence:.2f} < {DISCOVERY_FLOOR})",
            )
            stats["rejected"] += 1
            continue

        # Materialise the Org if it isn't in the graph yet.
        org_id = candidate.org_id or ""
        if not org_id:
            warnings.append(
                f"rescue_refiner: rescue of {candidate.membership_id!r} "
                "skipped (candidate had no org_id)",
            )
            stats["rejected"] += 1
            continue
        if org_id not in org_ids:
            org_name = candidate.org_name or org_id
            id_source = (
                "pulse:ror" if org_id.startswith("https://ror.org/")
                else "pulse:infoscienceOrganizationIdentifier" if org_id.startswith(
                    "https://infoscience.epfl.ch/",
                )
                else "pulse:githubOrganizationHandle" if org_id.startswith(
                    "https://github.com/",
                )
                else "uuid"
            )
            org_stub = {
                "id": org_id,
                "type": "org:Organization",
                "shacl": "pulse:OrganizationShape",
                "identifiers": {
                    "pulse:ror": org_id if org_id.startswith("https://ror.org/") else None,
                    "pulse:infoscienceOrganizationIdentifier": (
                        org_id if org_id.startswith("https://infoscience.epfl.ch/") else None
                    ),
                    "pulse:githubOrganizationHandle": (
                        org_id.removeprefix("https://github.com/")
                        if org_id.startswith("https://github.com/")
                        else None
                    ),
                },
                "idSource": id_source,
                "schema:name": org_name,
                "schema:identifier": (
                    org_id if org_id.startswith("https://ror.org/") else None
                ),
                "pulse:OrganizationType": "pulse:OtherOrganizationType",
                "_source": "hybrid_rescue_refiner",
                "_rescue_reason": decision.reason[:240] if decision.reason else "",
                "_rescue_confidence": decision.confidence,
            }
            new_orgs.append(org_stub)
            org_ids.add(org_id)

        # Materialise the Membership. Role/dates left None — the
        # rescue evidence lives in `_rescue_reason` and the warning.
        new_memberships.append(
            {
                "id": candidate.membership_id,
                "type": "org:Membership",
                "shacl": "pulse:MembershipShape",
                "identifiers": {
                    "pulse:composite": candidate.membership_id,
                },
                "idSource": "pulse:composite",
                "org:organization": org_id,
                "org:role": None,
                "time:hasBeginning": None,
                "time:hasEnd": None,
                "_person_ref": candidate.person_id,
                "_source": "hybrid_rescue_refiner",
                "_rescue_reason": decision.reason[:240] if decision.reason else "",
                "_rescue_confidence": decision.confidence,
            },
        )
        accepted_membership_ids.add(candidate.membership_id)
        stats["rescued"] += 1
        warnings.append(
            f"rescue_refiner: rescued Membership {candidate.membership_id!r} "
            f"(person={candidate.person_name!r}, org={candidate.org_name!r}, "
            f"confidence={decision.confidence:.2f}, reason={decision.reason[:120]!r})",
        )

    if new_orgs:
        reconciled.entities["organizations"] = organizations + new_orgs
    if new_memberships:
        reconciled.memberships = list(reconciled.memberships) + new_memberships

    # Reattach to the Person's org:hasMembership list so the graph
    # walks remain consistent with the rescue.
    for membership in new_memberships:
        pid = membership.get("_person_ref")
        person = persons_by_id.get(pid) if isinstance(pid, str) else None
        if not isinstance(person, dict):
            continue
        existing_refs = person.get("org:hasMembership")
        if not isinstance(existing_refs, list):
            existing_refs = []
            person["org:hasMembership"] = existing_refs
        ref = {"@id": membership["id"]}
        if ref not in existing_refs and membership["id"] not in [
            r.get("@id") if isinstance(r, dict) else r for r in existing_refs
        ]:
            existing_refs.append(ref)

    return (warnings, stats)


async def _run_discovery_pass(
    *,
    reconciled: ReconciledEntities,
    repo_context_summary: dict[str, Any],
    gathered_context: dict[str, Any] | None,
) -> tuple[list[str], dict[str, int]]:
    """Single LLM call that proposes new Persons/Orgs/Articles from
    README + CITATION.cff. Mutates `reconciled` in place to append the
    accepted entities. Returns (warnings, stats)."""
    stats = {"proposed": 0, "added": 0, "rejected": 0}

    persons = reconciled.entities.get("persons") or []
    organizations = reconciled.entities.get("organizations") or []
    articles = reconciled.entities.get("articles") or []
    existing_person_ids = {p["id"] for p in persons if isinstance(p.get("id"), str)}
    existing_org_ids = {o["id"] for o in organizations if isinstance(o.get("id"), str)}
    existing_article_ids = {a["id"] for a in articles if isinstance(a.get("id"), str)}

    repo_handle = ""
    readme_text: str | None = None
    citation_cff: str | None = None
    repo_description: str | None = None
    repo_topics: list[str] = []
    aux_files: dict[str, str] = {}
    if isinstance(gathered_context, dict):
        repo_ctx = gathered_context.get("repository") or {}
        if isinstance(repo_ctx, dict):
            repo_handle = repo_ctx.get("full_name") or ""
            readme_value = repo_ctx.get("readme_content")
            if isinstance(readme_value, str) and readme_value.strip():
                readme_text = readme_value[:DISCOVERY_README_CAP]
            aux_value = repo_ctx.get("aux_files")
            if isinstance(aux_value, dict):
                aux_files = {
                    k: v for k, v in aux_value.items()
                    if isinstance(v, str) and v.strip()
                }
            cff_from_aux = aux_files.get("CITATION.cff") or aux_files.get("citation.cff")
            if isinstance(cff_from_aux, str) and cff_from_aux.strip():
                citation_cff = cff_from_aux[:DISCOVERY_CITATION_CAP]
            metadata = repo_ctx.get("metadata") or {}
            if isinstance(metadata, dict):
                if isinstance(metadata.get("description"), str):
                    repo_description = metadata["description"]
                if isinstance(metadata.get("topics"), list):
                    repo_topics = [t for t in metadata["topics"] if isinstance(t, str)]
                if not citation_cff:
                    cff = metadata.get("citation_cff") or metadata.get("CITATION_cff")
                    if isinstance(cff, str) and cff.strip():
                        citation_cff = cff[:DISCOVERY_CITATION_CAP]

    if not readme_text and not citation_cff and not aux_files:
        return ([], stats)  # nothing to inspect

    refiner = DiscoveryRefinerAgent()
    refiner_input = DiscoveryRefinerInput(
        repo_handle=repo_handle,
        readme_text=readme_text,
        citation_cff=citation_cff,
        repo_description=repo_description,
        repo_topics=repo_topics,
        aux_files=aux_files,
        existing_person_ids=sorted(existing_person_ids),
        existing_org_ids=sorted(existing_org_ids),
        existing_article_ids=sorted(existing_article_ids),
    )
    warnings: list[str] = []
    try:
        proposal = await refiner.run(refiner_input=refiner_input)
    except LLMRuntimeError as exc:
        warnings.append(f"discovery_refiner: skipped — {exc}")
        return (warnings, stats)
    except Exception as exc:  # noqa: BLE001
        logger.exception("discovery_refiner crashed")
        warnings.append(f"discovery_refiner: skipped (unexpected error) — {exc}")
        return (warnings, stats)

    stats["proposed"] = (
        len(proposal.new_persons)
        + len(proposal.new_orgs)
        + len(proposal.new_articles)
    )

    def _accept(
        entity: dict[str, Any] | None,
        warning: str | None,
        bucket: list[dict[str, Any]],
        bucket_ids: set[str],
    ) -> None:
        if entity is None:
            if warning:
                warnings.append(warning)
            stats["rejected"] += 1
            return
        bucket.append(entity)
        bucket_ids.add(entity["id"])
        stats["added"] += 1
        warnings.append(
            f"discovery_refiner: added {entity['type']} {entity['id']} "
            f"(confidence={entity['_discovery_confidence']:.2f}, "
            f"reason={entity['_discovery_reason']!r})",
        )

    person_bucket: list[dict[str, Any]] = list(persons)
    org_bucket: list[dict[str, Any]] = list(organizations)
    article_bucket: list[dict[str, Any]] = list(articles)
    for p in proposal.new_persons[:DISCOVERY_REPLY_MAX_PER_TYPE]:
        e, w = _materialize_person(p, existing_person_ids)
        _accept(e, w, person_bucket, existing_person_ids)
    for o in proposal.new_orgs[:DISCOVERY_REPLY_MAX_PER_TYPE]:
        e, w = _materialize_org(o, existing_org_ids)
        _accept(e, w, org_bucket, existing_org_ids)
    for a in proposal.new_articles[:DISCOVERY_REPLY_MAX_PER_TYPE]:
        e, w = _materialize_article(a, existing_article_ids)
        _accept(e, w, article_bucket, existing_article_ids)

    # Write back only when something was added.
    if stats["added"]:
        reconciled.entities["persons"] = person_bucket
        reconciled.entities["organizations"] = org_bucket
        reconciled.entities["articles"] = article_bucket

    return (warnings, stats)


async def run_refine_with_llm_stage(  # noqa: PLR0913, PLR0915
    *,
    reconciled: ReconciledEntities,
    gathered_context: dict[str, Any] | None,
    org_refiner: OrganizationRefinerAgent | None = None,
    repo_refiner: RepositoryRefinerAgent | None = None,
    person_refiner: PersonRefinerAgent | None = None,
    membership_refiner: MembershipRefinerAgent | None = None,
    epfl_graph_provider: EpflGraphRagProvider | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> RefineWithLLMResult:
    """Run the hybrid LLM refinement stage over reconciled entities.

    Mutates ``reconciled`` in place — the caller receives the same instance back.
    """

    organizations = reconciled.entities.get("organizations") or []
    repositories = reconciled.entities.get("repositories") or []
    persons = reconciled.entities.get("persons") or []
    memberships_with_role = [m for m in reconciled.memberships if m.get("org:role")]

    if not (organizations or repositories or persons or memberships_with_role):
        return RefineWithLLMResult(reconciled=reconciled)

    org_refiner = org_refiner or OrganizationRefinerAgent()
    repo_refiner = repo_refiner or RepositoryRefinerAgent()
    person_refiner = person_refiner or PersonRefinerAgent()
    membership_refiner = membership_refiner or MembershipRefinerAgent()

    repo_context_summary = _build_repo_context_summary(gathered_context=gathered_context)
    raw_epfl_graph_hits = await _fetch_epfl_graph_hits(
        provider=epfl_graph_provider,
        repo_context_summary=repo_context_summary,
    )
    min_score = _epfl_graph_min_score()
    epfl_graph_hits = _filter_hits_by_score(raw_epfl_graph_hits, min_score=min_score)
    if raw_epfl_graph_hits:
        logger.info(
            "refine_with_llm: EPFL Graph hits prefetched count=%d kept=%d "
            "min_score=%.2f top_score=%.3f",
            len(raw_epfl_graph_hits),
            len(epfl_graph_hits),
            min_score,
            raw_epfl_graph_hits[0].get("score") or 0.0,
        )
    repo_context_summary_for_repo = (
        {**repo_context_summary, "epfl_graph_hits": epfl_graph_hits}
        if epfl_graph_hits
        else repo_context_summary
    )
    neighbors_tool = make_get_entity_neighbors_tool(
        entities=reconciled.entities,
        memberships=reconciled.memberships,
        contributions=reconciled.contributions,
    )
    semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    org_name_index = {
        org["id"]: org.get("schema:name", "")
        for org in organizations
        if isinstance(org.get("id"), str)
    }

    started_at = perf_counter()
    tasks: list[Any] = []
    routing: list[tuple[str, frozenset[str]]] = []

    for organization in organizations:
        tasks.append(
            _refine_org(
                organization=organization,
                refiner=org_refiner,
                repo_context_summary=repo_context_summary,
                neighbors_tool=neighbors_tool,
                semaphore=semaphore,
            ),
        )
        routing.append(("organization", ORG_PATCHABLE_FIELDS))

    for repository in repositories:
        tasks.append(
            _refine_repo(
                repository=repository,
                refiner=repo_refiner,
                repo_context_summary=repo_context_summary_for_repo,
                neighbors_tool=neighbors_tool,
                semaphore=semaphore,
            ),
        )
        routing.append(("repository", REPO_PATCHABLE_FIELDS))

    for person in persons:
        tasks.append(
            _refine_person(
                person=person,
                refiner=person_refiner,
                repo_context_summary=repo_context_summary,
                neighbors_tool=neighbors_tool,
                semaphore=semaphore,
            ),
        )
        routing.append(("person", PERSON_PATCHABLE_FIELDS))

    for membership in memberships_with_role:
        tasks.append(
            _refine_membership(
                membership=membership,
                refiner=membership_refiner,
                organization_name=_resolve_org_name_for_membership(
                    membership=membership,
                    org_index=org_name_index,
                ),
                semaphore=semaphore,
            ),
        )
        routing.append(("membership", MEMBERSHIP_PATCHABLE_FIELDS))

    results = await asyncio.gather(*tasks, return_exceptions=False)

    warnings: list[str] = []
    by_type: dict[str, dict[str, int]] = {
        "organization": {"refined": 0, "skipped": 0, "failed": 0},
        "repository": {"refined": 0, "skipped": 0, "failed": 0},
        "person": {"refined": 0, "skipped": 0, "failed": 0},
        "membership": {"refined": 0, "skipped": 0, "failed": 0},
    }
    for (type_label, whitelist), (entity, patch, run_warnings) in zip(
        routing,
        results,
        strict=True,
    ):
        warnings.extend(run_warnings)
        if run_warnings and not patch:
            by_type[type_label]["failed"] += 1
            continue
        if type_label == "repository":
            patch, gate_warnings = _gate_repo_type_patch(repository=entity, patch=patch)
            warnings.extend(gate_warnings)
        if not patch:
            by_type[type_label]["skipped"] += 1
            continue
        changed, merge_warnings = _apply_patch(
            entity=entity,
            patch=patch,
            whitelist=whitelist,
            type_label=type_label,
        )
        warnings.extend(merge_warnings)
        if changed:
            by_type[type_label]["refined"] += 1
        else:
            by_type[type_label]["skipped"] += 1

    # Rescue pass: ask the LLM to re-instate Memberships the
    # deterministic filter dropped, when the README / CITATION.cff
    # explicitly supports the person→org link. This is the "additive
    # but targeted" counterpart to the open-ended discovery pass.
    rescue_warnings, rescue_stats = await _run_rescue_pass(
        reconciled=reconciled,
        gathered_context=gathered_context,
    )
    warnings.extend(rescue_warnings)
    by_type["rescue"] = rescue_stats

    # Additive pass: ask the LLM what's MISSING from the graph relative
    # to the README / CITATION.cff. Each proposal must clear the
    # `DISCOVERY_CONFIDENCE_FLOOR`, carry a verifiable identifier, and
    # avoid duplicating existing @ids. Materialised entities flow into
    # the same `reconciled` container the per-entity refiners returned.
    discovery_warnings, discovery_stats = await _run_discovery_pass(
        reconciled=reconciled,
        repo_context_summary=repo_context_summary,
        gathered_context=gathered_context,
    )
    warnings.extend(discovery_warnings)
    by_type["discovery"] = discovery_stats

    refined_count = sum(stats.get("refined", 0) for stats in by_type.values())
    skipped_count = sum(stats.get("skipped", 0) for stats in by_type.values())
    failed_count = sum(stats.get("failed", 0) for stats in by_type.values())

    logger.info(
        "refine_with_llm: refined=%d skipped=%d failed=%d "
        "(orgs=%s repos=%s persons=%s memberships=%s) in %.2fs",
        refined_count,
        skipped_count,
        failed_count,
        by_type["organization"],
        by_type["repository"],
        by_type["person"],
        by_type["membership"],
        perf_counter() - started_at,
    )

    return RefineWithLLMResult(
        reconciled=reconciled,
        warnings=warnings,
        refined_count=refined_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        by_type=by_type,
    )


__all__ = [
    "MEMBERSHIP_PATCHABLE_FIELDS",
    "ORG_PATCHABLE_FIELDS",
    "PERSON_PATCHABLE_FIELDS",
    "REPO_PATCHABLE_FIELDS",
    "RefineWithLLMResult",
    "is_enabled",
    "run_refine_with_llm_stage",
]
