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
import re
from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import uuid4

import requests

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
from src.v2.agents.llm.refiners.org_resolver import (
    OrgResolverAgent,
    OrgResolverInput,
    OrgResolverPatch,
    UnresolvedOrg,
)
from src.v2.agents.llm.refiners.repo_signals.agent import (
    RepoSignalsInput,
    run_repo_signals,
)
from src.v2.agents.llm.refiners.rescue import (
    RescueCandidate,
    RescueRefinerAgent,
    RescueRefinerInput,
)
from src.v2.agents.llm.runtime import LLMRuntimeError
from src.v2.agents.rule_based._repo_signals import extract_doc_candidate_urls
from src.v2.api_models.enums import OrganizationTypeV2
from src.v2.ingest.providers.epfl_graph_rag import EpflGraphRagProvider
from src.v2.parsers.citation_cff import parse_citation_cff
from src.v2.parsers.publiccode import parse_publiccode
from src.v2.pipeline.stages.models import ReconciledEntities

logger = logging.getLogger(__name__)

ORG_PATCHABLE_FIELDS: frozenset[str] = frozenset({"pulse:OrganizationType"})
REPO_PATCHABLE_FIELDS: frozenset[str] = frozenset(
    {"pulse:discipline", "pulse:repositoryType"},
)
PERSON_PATCHABLE_FIELDS: frozenset[str] = frozenset({"schema:name"})
MEMBERSHIP_PATCHABLE_FIELDS: frozenset[str] = frozenset({"org:role"})

README_CONTEXT_MAX_CHARS = 1500
# Aux file excerpts are short enough that we can ship more characters
# than the README (the LLM cares about author names / institutional
# affiliations / cited DOIs, which live in the first ~2-3 KB).
AUX_FILE_CONTEXT_MAX_CHARS = 4_000
# Case-insensitive filenames whose contents we forward to the LLM
# refiners as additional context. Mirrors the agent-level URL pointers
# in `repository_agent._REPO_AUX_FILE_LOOKUPS` (CITATION.cff / AUTHORS /
# CONTRIBUTING.md / publiccode.yml). The key in the LLM context summary
# is the slug; the value is the (capped) file content.
_AUX_FILE_CONTEXT_LOOKUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("citation_cff",  ("citation.cff",)),
    ("authors",       ("authors", "authors.md", "authors.rst", "authors.txt")),
    ("contributing",  ("contributing.md", "contribution.md")),
    ("publiccode",    ("publiccode.yml", "publiccode.yaml")),
    ("security",      ("security.md",)),
)
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

    # Forward excerpts of supplementary attribution / governance /
    # public-sector metadata files so the LLM refiners can quote
    # author names and DOIs straight out of CITATION.cff and AUTHORS
    # instead of having to re-derive them from the README.
    aux_files = repository_context.get("aux_files")
    if isinstance(aux_files, dict) and aux_files:
        lower_to_original = {
            str(name).lower(): name
            for name in aux_files
            if isinstance(name, str)
        }
        aux_excerpts: dict[str, str] = {}
        for slug, candidates in _AUX_FILE_CONTEXT_LOOKUPS:
            for candidate in candidates:
                original = lower_to_original.get(candidate)
                if original is None:
                    continue
                content = aux_files.get(original)
                if isinstance(content, str) and content.strip():
                    aux_excerpts[slug] = content[:AUX_FILE_CONTEXT_MAX_CHARS]
                    break  # first match wins per slug
        if aux_excerpts:
            summary["aux_files"] = aux_excerpts

        # When a publiccode.yml is present, also surface the *parsed*
        # payload so the LLM gets typed fields (license, softwareType,
        # repoOwner, contacts) instead of having to reparse YAML. The
        # raw excerpt above remains so the LLM can verify a quote
        # verbatim if it needs to.
        publiccode_filename = lower_to_original.get(
            "publiccode.yml",
        ) or lower_to_original.get("publiccode.yaml")
        if publiccode_filename:
            content = aux_files.get(publiccode_filename)
            if isinstance(content, str):
                parsed = parse_publiccode(content)
                if parsed:
                    summary["publiccode"] = parsed

        # Same treatment for CITATION.cff — parsed payload alongside
        # the raw excerpt so LLM refiners can read typed authors /
        # identifiers / preferred-citation without YAML-grepping.
        citation_filename = lower_to_original.get("citation.cff")
        if citation_filename:
            content = aux_files.get(citation_filename)
            if isinstance(content, str):
                parsed_cff = parse_citation_cff(content)
                if parsed_cff:
                    summary["citation_cff"] = parsed_cff

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
    """Return canonical ORCID URL for use as a dedup key. v3.0.0:
    URL form matches entity field values."""
    from src.v2.canonicalization.orcid import orcid_iri

    return orcid_iri(value)


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


# Strict ArticleShape requires `schema:identifier` to match this pattern
# (bare DOI, not the URL form). LLM-emitted proposals tend to give the
# URL form; we keep it for the entity's @id but strip it for the
# identifier slot.
_BARE_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+$")


def _extract_bare_doi(value: str | None) -> str | None:
    """`https://doi.org/10.x/y` → `10.x/y`; passes through valid bare DOIs."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.startswith("https://doi.org/"):
        candidate = candidate[len("https://doi.org/"):]
    elif candidate.startswith("doi:"):
        candidate = candidate[4:]
    if _BARE_DOI_PATTERN.match(candidate):
        return candidate
    return None


_OPENALEX_BASE_URL = "https://api.openalex.org"


def _openalex_lookup_doi(bare_doi: str) -> dict[str, Any] | None:
    """Resolve a DOI to OpenAlex `publication_date` + author list.

    Discovery articles arrive with only a DOI and a quoted README/aux
    snippet — they don't carry the structured authorship needed for
    ArticleShape's `schema:author` (minCount=1) or the required
    `schema:datePublished`. OpenAlex's `/works/doi:<bare>` returns
    both, with each authorship carrying an ORCID we can match against
    existing Persons in the graph (avoiding duplicates) or use to
    materialise a Person stub for unmatched authors.

    Returns:
      {
        "publication_date": "YYYY-MM-DD" | None,
        "authorships": [{"name": "...", "orcid": "https://orcid.org/..." | None}],
      }
    Or None on any failure (network, 404, malformed). The article
    materialiser drops the proposal when this is None.
    """
    mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
    url = f"{_OPENALEX_BASE_URL}/works/doi:{bare_doi}"
    params = {"mailto": mailto} if mailto else None
    try:
        response = requests.get(url, params=params, timeout=15)
    except Exception:  # noqa: BLE001
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    authorships: list[dict[str, str | None]] = []
    for raw in data.get("authorships") or []:
        if not isinstance(raw, dict):
            continue
        author = raw.get("author") or {}
        if not isinstance(author, dict):
            continue
        name = author.get("display_name")
        orcid = author.get("orcid")
        if not isinstance(name, str) or not name.strip():
            continue
        authorships.append({
            "name": name.strip(),
            "orcid": orcid.strip() if isinstance(orcid, str) and orcid.strip() else None,
        })
    return {
        "publication_date": data.get("publication_date"),
        "authorships": authorships,
    }


# GitHub handle suffixes that flag the account as a lab / group / department
# rather than an individual researcher. AUTHORS lines like
# "M Mathis, mackenzie@post.harvard.edu | https://github.com/MMathisLab"
# pair a person's name with a lab-branded handle — modelling this as a
# Person creates a phantom duplicate of the ORCID-anchored Person while
# losing the lab→institution relation. Reclassifying to org:Organization
# lets us emit `org:unitOf` toward the real institution.
_LAB_HANDLE_SUFFIXES: tuple[str, ...] = (
    "lab", "labs",
    "group",
    "team",
    "center", "centre",
    "institute",
    "consortium",
    "network",
)


def _looks_lab_flavored(handle: str | None) -> bool:
    """Heuristic: GitHub handle whose suffix screams "this is a lab/group"."""
    if not isinstance(handle, str):
        return False
    normalized = handle.strip().lower()
    if not normalized:
        return False
    return any(normalized.endswith(suffix) for suffix in _LAB_HANDLE_SUFFIXES)


def _build_person_dedup_index(
    persons: list[dict[str, Any]],
) -> dict[str, str]:
    """Build (handle | ORCID | email) → canonical @id map for existing
    persons.

    The discovery refiner's existing @id-equality check is not enough:
    rule-based persons are commonly keyed on ORCID
    (`https://orcid.org/X-X-X-X`) while discovery proposes them by their
    GitHub URL (`https://github.com/<handle>`) — different @ids for the
    same human. This index lets us catch those duplicates before
    materialisation, so we drop them rather than re-emit a phantom.
    """

    index: dict[str, str] = {}
    for person in persons:
        if not isinstance(person, dict):
            continue
        canonical = person.get("id") or person.get("@id")
        if not isinstance(canonical, str) or not canonical:
            continue
        handle = person.get("pulse:githubUsername")
        if isinstance(handle, str) and handle.strip():
            index.setdefault(f"gh:{handle.strip().lower()}", canonical)
        orcid_url = person.get("pulse:orcidIdentifier")
        if isinstance(orcid_url, str):
            match = re.search(
                r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])",
                orcid_url,
                re.IGNORECASE,
            )
            if match:
                index.setdefault(f"orcid:{match.group(1).upper()}", canonical)
        email = person.get("schema:email")
        if isinstance(email, str) and "@" in email:
            index.setdefault(f"email:{email.strip().lower()}", canonical)
    return index


def _infer_lab_parent_org(
    person_name: str | None,
    persons: list[dict[str, Any]],
    memberships: list[dict[str, Any]],
) -> str | None:
    """When discovery promotes a lab-flavoured handle to an Org, link it
    via `org:unitOf` to the institution its named owner already belongs
    to.

    Strategy: loose-match `person_name` (tokens length ≥ 3, ≥ 2 shared
    with an existing person), then return the @id of that person's
    first kept Membership organisation. Returns None if no confident
    match — caller will emit the Org without `org:unitOf` rather than
    guess.
    """

    if not isinstance(person_name, str):
        return None
    target = person_name.strip().lower()
    if not target:
        return None
    target_tokens = set(re.findall(r"\w{3,}", target))
    if not target_tokens:
        return None

    matched_person_ids: list[str] = []
    for person in persons:
        if not isinstance(person, dict):
            continue
        candidate_name = (person.get("schema:name") or "").strip().lower()
        if not candidate_name:
            continue
        candidate_tokens = set(re.findall(r"\w{3,}", candidate_name))
        if len(target_tokens & candidate_tokens) >= 2:
            canonical = person.get("id") or person.get("@id")
            if isinstance(canonical, str) and canonical:
                matched_person_ids.append(canonical)

    if not matched_person_ids:
        return None

    matched_set = set(matched_person_ids)
    for membership in memberships:
        if not isinstance(membership, dict):
            continue
        person_ref = membership.get("_person_ref")
        if person_ref not in matched_set:
            continue
        org_ref = membership.get("org:organization")
        if isinstance(org_ref, dict):
            org_ref = org_ref.get("@id") or org_ref.get("id")
        if isinstance(org_ref, str) and org_ref.strip():
            return org_ref
    return None


def _materialize_person(
    proposal: DiscoveredPerson,
    existing_ids: set[str],
    dedup_index: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Convert a DiscoveredPerson into a SHACL-compliant Person entity.

    Returns (entity_dict, warning_or_none). When the proposal can't be
    materialised (missing identifier, duplicate id, low confidence), the
    entity is None and the warning explains why. `dedup_index` lets us
    catch cross-identifier duplicates (handle ↔ ORCID ↔ email pointing
    at the same human under different @ids) — those get dropped rather
    than linked, per the project's "stay within the ontology, no
    `owl:sameAs` until reviewed" policy.
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
    if dedup_index:
        if github_handle:
            existing_canonical = dedup_index.get(f"gh:{github_handle.lower()}")
            if existing_canonical:
                return None, (
                    f"discovery_refiner: dropped Person proposal {proposal.schema_name!r} "
                    f"(handle @{github_handle} already in graph as {existing_canonical})"
                )
        if orcid:
            existing_canonical = dedup_index.get(f"orcid:{orcid.upper()}")
            if existing_canonical:
                return None, (
                    f"discovery_refiner: dropped Person proposal {proposal.schema_name!r} "
                    f"(ORCID {orcid} already in graph as {existing_canonical})"
                )
        email = (proposal.schema_email or "").strip().lower()
        if email and "@" in email:
            existing_canonical = dedup_index.get(f"email:{email}")
            if existing_canonical:
                return None, (
                    f"discovery_refiner: dropped Person proposal {proposal.schema_name!r} "
                    f"(email {email} already in graph as {existing_canonical})"
                )
    return (
        {
            "id": person_id,
            "type": "schema:Person",
            "shacl": "pulse:PersonShape",
            "identifiers": {
                # `uuid` is a SHACL-required identifier for every Person — the
                # strict validator rejects entities missing it. Rule-based
                # persons get one stamped during reconciliation (see
                # reconciliation.py around line 137); discovery-materialised
                # persons never went through that stage so we mint one here.
                "uuid": str(uuid4()),
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
                # SHACL-required: mirrors the rule-based Org reconciliation
                # path which always stamps a uuid (reconciliation.py:206).
                "uuid": str(uuid4()),
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
    existing_article_ids: set[str],
    existing_person_ids: set[str],
    person_dedup_index: dict[str, str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    """Materialise a DiscoveredArticle with OpenAlex enrichment.

    Strict `ArticleShape` requires three things the LLM doesn't reliably
    produce:
    - `schema:identifier` matching `^10\\.\\d{4,9}/...$` (BARE DOI, not
      the `https://doi.org/...` URL form).
    - `schema:datePublished` (`xsd:date`).
    - `schema:author` with `minCount=1` (Person references).

    Strategy: parse the DOI, look it up on OpenAlex for the publication
    date and authorship list, then match each authorship's ORCID
    against the existing graph (via `person_dedup_index`) so we don't
    duplicate Persons. Authors without an ORCID can't be uniquely
    referenced — they're skipped. If we end up with zero resolvable
    author refs after dedup we drop the article (better than emitting
    a SHACL-invalid one).

    Returns `(article, new_person_stubs, warning)`. The caller is
    responsible for appending `new_person_stubs` to the person bucket
    and updating `existing_person_ids` / `person_dedup_index`.
    """

    if proposal.confidence < DISCOVERY_CONFIDENCE_FLOOR:
        return None, [], (
            f"discovery_refiner: dropped Article proposal {proposal.schema_name!r} "
            f"(confidence={proposal.confidence:.2f} < {DISCOVERY_CONFIDENCE_FLOOR})"
        )
    bare_doi = _extract_bare_doi(proposal.schema_identifier)
    if not bare_doi:
        return None, [], (
            f"discovery_refiner: dropped Article proposal {proposal.schema_name!r} "
            f"(missing/malformed DOI: {proposal.schema_identifier!r})"
        )
    doi_url = f"https://doi.org/{bare_doi}"
    if doi_url in existing_article_ids or bare_doi in existing_article_ids:
        return None, [], (
            f"discovery_refiner: dropped Article proposal {proposal.schema_name!r} "
            f"(DOI {bare_doi} already in graph)"
        )

    enrichment = _openalex_lookup_doi(bare_doi)
    if enrichment is None:
        return None, [], (
            f"discovery_refiner: dropped Article proposal {proposal.schema_name!r} "
            f"(OpenAlex lookup failed for DOI {bare_doi})"
        )

    pub_date = enrichment.get("publication_date") or proposal.schema_datePublished
    if not isinstance(pub_date, str) or not pub_date.strip():
        return None, [], (
            f"discovery_refiner: dropped Article proposal {proposal.schema_name!r} "
            f"(no publication date for DOI {bare_doi})"
        )

    author_refs: list[dict[str, str]] = []
    new_persons: list[dict[str, Any]] = []
    seen_author_ids: set[str] = set()
    for authorship in enrichment.get("authorships", []) or []:
        orcid_url = authorship.get("orcid")
        name = authorship.get("name")
        if not isinstance(orcid_url, str):
            # Without an ORCID we have no stable identifier to dedup
            # against the existing graph; skip the author rather than
            # emit a name-only Person ref that strict validation rejects.
            continue
        match = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", orcid_url, re.IGNORECASE)
        if not match:
            continue
        bare_orcid = match.group(1).upper()
        canonical_id = person_dedup_index.get(f"orcid:{bare_orcid}")
        if canonical_id is None:
            canonical_id = f"https://orcid.org/{bare_orcid}"
            if canonical_id not in existing_person_ids:
                new_persons.append({
                    "id": canonical_id,
                    "type": "schema:Person",
                    "shacl": "pulse:PersonShape",
                    "identifiers": {
                        "uuid": str(uuid4()),
                        "pulse:orcid": canonical_id,
                    },
                    "idSource": "pulse:orcid",
                    "schema:name": name,
                    "pulse:orcidIdentifier": canonical_id,
                    "_source": "hybrid_refiner",
                    "_discovery_reason": f"OpenAlex authorship of {bare_doi}",
                    "_discovery_confidence": proposal.confidence,
                })
        if canonical_id in seen_author_ids:
            continue
        seen_author_ids.add(canonical_id)
        author_refs.append({"@id": canonical_id})

    if not author_refs:
        return None, [], (
            f"discovery_refiner: dropped Article proposal {proposal.schema_name!r} "
            f"(OpenAlex returned no ORCID-anchored authors for DOI {bare_doi})"
        )

    article = {
        "id": doi_url,
        "type": "schema:ScholarlyArticle",
        "shacl": "pulse:ArticleShape",
        "identifiers": {
            "uuid": str(uuid4()),
            "schema:identifier": bare_doi,
        },
        "idSource": "schema:identifier",
        "schema:name": proposal.schema_name,
        "schema:identifier": bare_doi,
        "schema:datePublished": pub_date,
        "schema:author": author_refs,
        "_source": "hybrid_refiner",
        "_discovery_reason": proposal.reason[:240] if proposal.reason else "",
        "_discovery_confidence": proposal.confidence,
        "_proposed_author_names": list(proposal.author_names or []),
    }
    return article, new_persons, None


def _materialize_lab_org(
    proposal: DiscoveredPerson,
    existing_org_ids: set[str],
    parent_org_id: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Convert a discovery-proposed Person carrying a lab-flavoured
    GitHub handle into an `org:Organization` entity.

    AUTHORS lines like ``M Mathis, ... | https://github.com/MMathisLab``
    name a real human but the handle functions as a lab identity — it
    is the lab's GitHub account, not the person's personal one. The
    correct ontological move is to emit it as an Organization (with
    `pulse:githubOrganizationHandle`) and, when we can identify the
    parent institution from the named person's existing Memberships,
    link it via `org:unitOf`. We deliberately do NOT add `owl:sameAs`
    between the lab Org and the underlying Person — that pattern is
    under review with the ontology owner.
    """

    handle = _normalize_github_handle(proposal.pulse_githubUsername)
    if not handle:
        return None, (
            f"discovery_refiner: dropped lab-Org proposal {proposal.schema_name!r} "
            "(no github handle)"
        )
    org_id = f"https://github.com/{handle}"
    if org_id in existing_org_ids:
        return None, (
            f"discovery_refiner: dropped lab-Org proposal {proposal.schema_name!r} "
            f"(@id {org_id} already in graph)"
        )

    # Display name: keep the AUTHORS-line name but tag the type. The
    # person's name is the most accurate label we have for a lab-flavoured
    # account at this stage; a richer rename pass can follow.
    entity: dict[str, Any] = {
        "id": org_id,
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "identifiers": {
            # SHACL-required uuid, mirrored from the standard Org path.
            "uuid": str(uuid4()),
            "pulse:githubOrganizationHandle": handle,
        },
        "idSource": "pulse:githubOrganizationHandle",
        "schema:name": proposal.schema_name,
        "pulse:githubOrganizationHandle": handle,
        # Lab/group accounts on GitHub almost always belong to a research
        # institution; falling back to the generic "Other" type is
        # uninformative. Caller can refine later if needed.
        "pulse:OrganizationType": "pulse:ResearchInstitution",
        "_source": "hybrid_refiner",
        "_discovery_reason": (proposal.reason or "")[:240],
        "_discovery_confidence": proposal.confidence,
        "_reclassified_from_person": True,
    }
    if parent_org_id:
        # Schema expects an array of string @ids (mirrors how
        # rule-based reconciliation populates `org:hasUnit` and
        # `org:unitOf`), not an array of `{"@id": …}` dicts.
        entity["org:unitOf"] = [parent_org_id]
    return entity, None


# A short uppercase code with no whitespace — Infoscience-style orgunit
# acronym (e.g. UPMWMATHIS, UPAMATHIS, IC-IINFCOM, ENAC-LMS). These map
# 1:1 in the Infoscience orgunit acronym index but vector embeddings
# can't pick them up.
_CODE_LIKE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{2,15}$")


def _looks_like_infoscience_code(query: str) -> bool:
    if not isinstance(query, str):
        return False
    return bool(_CODE_LIKE_PATTERN.fullmatch(query.strip()))


def _split_composite_org_name(query: str) -> tuple[str, str] | None:
    """`"CNRS, IGF"` → `("CNRS", "IGF")`. Returns None when it doesn't look composite."""

    if not isinstance(query, str):
        return None
    parts = [part.strip() for part in query.split(",")]
    if len(parts) != 2:
        return None
    if not all(parts):
        return None
    return parts[0], parts[1]


def _flatten_handle_concat(query: str) -> list[str]:
    """`"@a @b @c"` → `["a", "b", "c"]`. Empty list when not handle-shaped."""

    if not isinstance(query, str):
        return []
    candidates: list[str] = []
    for token in query.split():
        candidate = _normalize_github_handle(token)
        if candidate:
            candidates.append(candidate)
    return candidates


def _query_communities_index(query: str) -> list[dict[str, Any]]:
    """SQL lookup against the local `communities` DuckDB.

    Matches an org name against `source_slug`, `title`, and
    `description` (case-insensitive), preferring exact-slug hits.
    Also strips a leading `@` so GitHub-handle-shaped queries work.
    """

    if not isinstance(query, str) or not query.strip():
        return []
    try:
        import duckdb  # noqa: PLC0415

        from open_pulse_sources.index.zenodo_communities.paths import duckdb_path  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    db_path = duckdb_path()
    if not db_path.exists():
        return []
    q = query.strip().lstrip("@").strip()
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception:  # noqa: BLE001
        return []
    rows: list[dict[str, Any]] = []
    try:
        result = con.execute(
            """
            SELECT community_id, source_slug, parent_org, title, description, url
            FROM communities
            WHERE source_slug = ?
               OR LOWER(source_slug) = LOWER(?)
               OR title ILIKE ?
               OR description ILIKE ?
            ORDER BY
                CASE WHEN source_slug = ? THEN 0 ELSE 1 END,
                LENGTH(COALESCE(title, '')) ASC
            LIMIT 5
            """,
            [q, q, f"%{q}%", f"%{q}%", q],
        ).fetchall()
        columns = [d[0] for d in con.description]
        for row in result:
            rows.append(dict(zip(columns, row, strict=False)))
    except Exception:  # noqa: BLE001
        return []
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass
    return rows


def _query_infoscience_acronym(db_path: str, query: str) -> list[dict[str, Any]]:
    """Look up an Infoscience orgunit by acronym (exact match, then prefix).

    Returns a list of `{org_uuid, name, acronym, parent_org_uuid, url}`
    dicts. The lookup is intentionally narrow — only hits when the
    query has the code-shape (uppercase / alphanumeric / no whitespace);
    free-text names go through the live-API and federated branches.
    """

    import duckdb  # noqa: PLC0415

    if not _looks_like_infoscience_code(query):
        return []
    rows: list[dict[str, Any]] = []
    try:
        con = duckdb.connect(db_path, read_only=True)
    except Exception:  # noqa: BLE001
        return []
    try:
        result = con.execute(
            """
            SELECT org_uuid, name, acronym, parent_org_uuid
            FROM organizations
            WHERE acronym = ?
            LIMIT 5
            """,
            [query],
        ).fetchall()
        columns = [d[0] for d in con.description] if result else []
        for row in result:
            record = dict(zip(columns, row, strict=False))
            record["url"] = (
                f"https://infoscience.epfl.ch/entities/orgunit/{record['org_uuid']}"
            )
            rows.append(record)
    except Exception:  # noqa: BLE001
        return []
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass
    return rows


async def _gather_federated_evidence(
    query: str,
    *,
    providers: Any,
    max_per_source: int = 5,
    total_timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """One-shot "silver bullet" evidence pack for the org resolver LLM.

    Fans the query out concurrently to:
      1. Direct Infoscience `search_orgunit` — best for UP* / EPFL-*
         codes that fail semantic search.
      2. Direct GitHub `get_organization` — best for `@handle`-shaped
         queries; resolves display_name + description.
      3. ROR search for the parent half of `"<X>, <Y>"` composites.
      4. Federated RAG semantic search (12 indices in parallel).

    Each branch is wrapped in a per-call timeout AND the whole fan-out
    is bounded by `total_timeout_seconds` so a slow provider can't hang
    the resolver stage. Branches that fail or return empty contribute
    `[]` to their slot — the LLM downstream knows to ignore empty slots.

    The result is appended to the resolver's user prompt as
    `pre_fetched_evidence`, so the LLM gets a concrete starting point
    instead of having to discover the right tool routing on its own.
    """

    out: dict[str, Any] = {
        "query": query,
        "infoscience_duckdb_hits": [],
        "communities_hits": [],
        "infoscience_orgunit_hits": [],
        "github_org": None,
        "ror_hits_for_parent": [],
        "ror_hits_for_unit": [],
        "federated_indices": [],
    }

    if not isinstance(query, str) or not query.strip():
        return out
    normalized_query = query.strip()

    # --- DuckDB direct (FAST + AUTHORITATIVE for codes) ----------------
    async def _safe_duckdb_infoscience() -> None:
        """SQL `WHERE acronym = ?` against the local Infoscience DuckDB.

        After the ingest fix (parsers.py extracts `oairecerif.acronym`
        into the `acronym` column) and the one-shot backfill, this
        returns the exact orgunit for codes like `UPMWMATHIS` /
        `UPAMATHIS` / `U13781` in ~1ms with zero noise — bypassing the
        semantic-vector search's confusion on opaque acronyms.
        Best signal in the whole aggregator for code-shaped queries.
        """
        try:
            import duckdb  # noqa: PLC0415 — local import keeps the cold-path cost out of the hot path

            from open_pulse_sources.index.infoscience.paths import duckdb_path  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return
        db_path = duckdb_path()
        if not db_path.exists():
            return
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(
                    _query_infoscience_acronym, str(db_path), normalized_query,
                ),
                timeout=4.0,
            )
        except Exception:  # noqa: BLE001
            return
        out["infoscience_duckdb_hits"] = rows[:max_per_source]

    # --- Communities DuckDB (lab / group registry) ---------------------
    async def _safe_communities() -> None:
        """Search the local `communities` DuckDB.

        Best signal when the dropped org is a GitHub-shaped lab handle
        (`@AdaptiveMotorControlLab`) or a free-text lab name — the
        index merges curated EPFL/ETHZ/CERN/CERN-openlab slugs plus
        ~700 auto-discovered communities, all keyed by parent org.
        """
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(_query_communities_index, normalized_query),
                timeout=4.0,
            )
        except Exception:  # noqa: BLE001
            return
        out["communities_hits"] = rows[:max_per_source]

    # --- Direct calls (sync providers, run in threadpool) -------------
    async def _safe_infoscience() -> None:
        if getattr(providers, "infoscience", None) is None:
            return
        try:
            hits = await asyncio.wait_for(
                asyncio.to_thread(
                    providers.infoscience.search_orgunit, normalized_query,
                ),
                timeout=8.0,
            )
        except Exception:  # noqa: BLE001
            return
        flat: list[dict[str, Any]] = []
        for hit in (hits or [])[:max_per_source]:
            payload = hit.model_dump(mode="json") if hasattr(hit, "model_dump") else dict(hit)
            flat.append({
                "name": payload.get("name"),
                "acronym": payload.get("acronym"),
                "parentOrganization": payload.get("parentOrganization"),
                "url": payload.get("url"),
                "infoscienceOrgUnitIdentifier": payload.get(
                    "infoscienceOrgUnitIdentifier",
                ),
            })
        out["infoscience_orgunit_hits"] = flat

    async def _safe_github() -> None:
        github_provider = getattr(providers, "github", None)
        if github_provider is None:
            return
        handles = _flatten_handle_concat(normalized_query)
        if not handles:
            # Plain string with no `@` but might still be a handle if short
            stripped = normalized_query.lstrip("@").strip()
            if " " not in stripped and 1 < len(stripped) <= 40:
                handles = [stripped]
        for handle in handles[:3]:
            try:
                meta = await asyncio.wait_for(
                    asyncio.to_thread(github_provider.get_organization, handle),
                    timeout=8.0,
                )
            except Exception:  # noqa: BLE001
                continue
            if isinstance(meta, dict) and meta.get("schema:name"):
                out["github_org"] = {
                    "handle": handle,
                    "name": meta.get("schema:name") or meta.get("name"),
                    "description": meta.get("schema:description") or meta.get("description"),
                    "url": meta.get("schema:url") or meta.get("html_url"),
                }
                return  # First valid handle wins

    async def _safe_ror_composite() -> None:
        ror_rag = getattr(providers, "ror_rag", None)
        if ror_rag is None:
            return
        composite = _split_composite_org_name(normalized_query)
        if composite is None:
            return
        parent_q, unit_q = composite
        try:
            parent_res = await asyncio.wait_for(
                ror_rag.search(parent_q, top_k=3), timeout=8.0,
            )
        except Exception:  # noqa: BLE001
            parent_res = []
        try:
            unit_res = await asyncio.wait_for(
                ror_rag.search(unit_q, top_k=3), timeout=8.0,
            )
        except Exception:  # noqa: BLE001
            unit_res = []
        def _flat(records: Any) -> list[dict[str, Any]]:
            if not isinstance(records, list):
                return []
            return [
                {
                    "name": r.get("schema:name") or r.get("name"),
                    "id": r.get("@id") or r.get("id"),
                    "country": r.get("country"),
                }
                for r in records if isinstance(r, dict)
            ][:max_per_source]
        out["ror_hits_for_parent"] = _flat(parent_res)
        out["ror_hits_for_unit"] = _flat(unit_res)

    async def _safe_federated() -> None:
        federated = getattr(providers, "federated_rag", None)
        if federated is None:
            return
        try:
            res = await asyncio.wait_for(
                federated.search(normalized_query, top_k=max_per_source),
                timeout=10.0,
            )
        except Exception:  # noqa: BLE001
            return
        hits = res.get("hits") if isinstance(res, dict) else []
        flat: list[dict[str, Any]] = []
        for h in (hits or [])[:max_per_source]:
            if not isinstance(h, dict):
                continue
            flat.append({
                "index": h.get("_source_index") or h.get("index") or h.get("source"),
                "id": h.get("@id") or h.get("id"),
                "name": h.get("schema:name") or h.get("name") or h.get("display_name") or h.get("title"),
                "score": h.get("score") or h.get("_score"),
            })
        out["federated_indices"] = flat

    try:
        await asyncio.wait_for(
            asyncio.gather(
                _safe_duckdb_infoscience(),
                _safe_communities(),
                _safe_infoscience(),
                _safe_github(),
                _safe_ror_composite(),
                _safe_federated(),
                return_exceptions=False,
            ),
            timeout=total_timeout_seconds,
        )
    except TimeoutError:
        logger.warning(
            "federated_evidence: total timeout (%.1fs) for query %r",
            total_timeout_seconds, normalized_query,
        )
    return out


def _org_needs_resolution(org: dict[str, Any]) -> bool:
    """An org is un-anchored when none of the SHACL `sh:or` slots is filled.

    `pulse:OrganizationShape` requires AT LEAST one of:
      * `schema:identifier`
      * `pulse:githubOrganizationHandle`
      * `pulse:infoscienceOrganizationIdentifier`
    Anything that fails this disjunction gets SHACL-rejected and dropped
    from the final graph. The resolver targets exactly those orgs.
    """

    if not isinstance(org, dict):
        return False
    for key in (
        "schema:identifier",
        "pulse:githubOrganizationHandle",
        "pulse:infoscienceOrganizationIdentifier",
        "pulse:ror",
    ):
        value = org.get(key)
        if isinstance(value, str) and value.strip():
            return False
    return True


def _affiliated_person_names(
    org_id: str,
    persons: list[dict[str, Any]],
    memberships: list[dict[str, Any]],
) -> list[str]:
    """Persons linked to `org_id` via Membership — helps the LLM disambiguate."""

    person_ids: set[str] = set()
    for membership in memberships:
        if not isinstance(membership, dict):
            continue
        org_ref = membership.get("org:organization")
        if isinstance(org_ref, dict):
            org_ref = org_ref.get("@id") or org_ref.get("id")
        if org_ref != org_id:
            continue
        person_ref = membership.get("_person_ref")
        if isinstance(person_ref, str) and person_ref:
            person_ids.add(person_ref)

    names: list[str] = []
    for person in persons:
        if not isinstance(person, dict):
            continue
        pid = person.get("id") or person.get("@id")
        if pid not in person_ids:
            continue
        name = person.get("schema:name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def _apply_org_resolver_patch(
    org: dict[str, Any],
    patch: OrgResolverPatch,
) -> bool:
    """Stamp resolver-supplied identifiers onto the org entity in place.

    Returns True when at least one identifier slot was filled (and the
    entity will now clear the OrganizationShape `sh:or` branch). The @id
    is intentionally NOT changed — that would break every inbound
    Membership/hasUnit reference. The new identifiers piggyback as
    properties.
    """

    if patch.confidence < 0.7:
        return False

    identifiers = org.setdefault("identifiers", {})
    if not isinstance(identifiers, dict):
        identifiers = {}
        org["identifiers"] = identifiers

    # The strict schema's `identifiers` block has
    # `additionalProperties: false` — every key we write must already
    # exist in the allowed set: {uuid, pulse:ror, pulse:githubOrganizationHandle,
    # pulse:infoscienceOrganizationIdentifier}. Top-level `pulse:ror` is
    # NOT a property on Organization — only `identifiers.pulse:ror` is.
    # Pattern constraints:
    #   - schema:identifier  ⇒ must match `^https://ror\.org/[0-9a-z]{9}$`
    #     (NOT a github URL, NOT an infoscience URL).
    #   - identifiers.pulse:infoscienceOrganizationIdentifier ⇒ bare UUID4.
    changed = False
    ror_pattern = re.compile(r"^https://ror\.org/[0-9a-z]{9}$")
    infoscience_uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )

    if patch.pulse_ror and ror_pattern.match(patch.pulse_ror):
        identifiers["pulse:ror"] = patch.pulse_ror
        # `schema:identifier` is constrained to the ROR URL form by
        # the strict schema; only set it when we actually have one.
        org["schema:identifier"] = patch.pulse_ror
        changed = True

    if patch.pulse_infoscienceOrganizationIdentifier:
        raw = patch.pulse_infoscienceOrganizationIdentifier.strip()
        # The LLM tends to return the full URL form even when prompted
        # for the bare UUID; strip the common URL prefixes.
        for prefix in (
            "https://infoscience.epfl.ch/entities/orgunit/",
            "https://infoscience.epfl.ch/server/api/core/items/",
            "https://infoscience.epfl.ch/",
        ):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        # Drop any trailing path segments / query strings.
        raw = raw.split("/", maxsplit=1)[0].split("?", maxsplit=1)[0]
        if infoscience_uuid_pattern.match(raw):
            identifiers["pulse:infoscienceOrganizationIdentifier"] = raw
            # Top-level mirror only if the schema has the property — it
            # doesn't, so leave it off. `sh:or` is satisfied by the
            # identifiers-block entry.
            changed = True

    if patch.pulse_githubOrganizationHandle:
        handle = patch.pulse_githubOrganizationHandle.strip().lstrip("@")
        if handle and " " not in handle:
            identifiers["pulse:githubOrganizationHandle"] = handle
            org["pulse:githubOrganizationHandle"] = handle
            changed = True

    if patch.pulse_OrganizationType:
        org["pulse:OrganizationType"] = patch.pulse_OrganizationType
        changed = True

    if patch.org_unitOf and patch.org_unitOf.startswith(("http://", "https://")):
        existing = org.get("org:unitOf")
        if isinstance(existing, list):
            if patch.org_unitOf not in existing:
                existing.append(patch.org_unitOf)
        else:
            org["org:unitOf"] = [patch.org_unitOf]
        changed = True

    if patch.schema_name_canonical and patch.schema_name_canonical.strip():
        # Keep original name as alias for traceability, swap the display.
        org["schema:name"] = patch.schema_name_canonical.strip()
        changed = True

    if changed:
        org["_source"] = org.get("_source") or "org_resolver"
        org["_org_resolver_reason"] = (patch.reason or "")[:240]
        org["_org_resolver_confidence"] = patch.confidence

    return changed


async def _run_org_resolver_pass(
    *,
    reconciled: ReconciledEntities,
    gathered_context: dict[str, Any] | None,
    providers: Any | None = None,
) -> tuple[list[str], dict[str, int]]:
    """LLM-driven resolution of orgs lacking any SHACL-required identifier.

    Runs BEFORE rescue so any newly anchored orgs (e.g. UPMWMATHIS →
    Infoscience id) become eligible Membership targets when rescue
    re-evaluates the evidence floor.
    """

    stats: dict[str, int] = {"candidates": 0, "resolved": 0, "declined": 0, "failed": 0}

    organizations = reconciled.entities.get("organizations") or []
    persons = reconciled.entities.get("persons") or []
    memberships = reconciled.memberships or []

    candidates = [
        org for org in organizations
        if isinstance(org, dict) and _org_needs_resolution(org)
    ]
    stats["candidates"] = len(candidates)
    if not candidates:
        return ([], stats)

    if providers is None:
        return (
            [
                f"org_resolver: skipped — providers not passed in; "
                f"{len(candidates)} un-anchored org(s) will fail strict validation",
            ],
            stats,
        )

    # Build the resolver toolset. Each guard mirrors the per-entity
    # refiner pattern: a missing index degrades silently.
    from src.v2.agents.llm.agent_tools.epfl_graph_rag import (  # noqa: PLC0415
        make_epfl_graph_rag_search_tool,
    )
    from src.v2.agents.llm.agent_tools.github_organization import (  # noqa: PLC0415
        make_github_organization_metadata_tool,
    )
    from src.v2.agents.llm.agent_tools.infoscience_rag import (  # noqa: PLC0415
        make_infoscience_rag_search_tool,
    )
    from src.v2.agents.llm.agent_tools.ror_rag import (  # noqa: PLC0415
        make_ror_rag_search_tool,
    )
    from src.v2.agents.llm.agent_tools.snsf_grants import (  # noqa: PLC0415
        make_search_snsf_grants_tool,
    )
    from src.v2.ingest.providers.snsf_grants import SnsfGrantsProvider  # noqa: PLC0415

    tools: list[Any] = []
    if getattr(providers, "ror_rag", None) is not None:
        tools.append(make_ror_rag_search_tool(providers.ror_rag))
    if getattr(providers, "infoscience_rag", None) is not None:
        tools.append(make_infoscience_rag_search_tool(providers.infoscience_rag))
    if getattr(providers, "epfl_graph_rag", None) is not None:
        tools.append(make_epfl_graph_rag_search_tool(providers.epfl_graph_rag))
    if getattr(providers, "github", None) is not None:
        tools.append(make_github_organization_metadata_tool(providers.github))
    tools.append(make_search_snsf_grants_tool(SnsfGrantsProvider()))

    if not tools:
        return (
            [
                f"org_resolver: skipped — no resolver tools available; "
                f"{len(candidates)} un-anchored org(s) will fail strict validation",
            ],
            stats,
        )

    # Repo context for disambiguation
    repo_handle = ""
    repo_description: str | None = None
    readme_excerpt: str | None = None
    if isinstance(gathered_context, dict):
        repo_ctx = gathered_context.get("repository") or {}
        if isinstance(repo_ctx, dict):
            repo_handle = repo_ctx.get("full_name") or ""
            readme = repo_ctx.get("readme_content")
            if isinstance(readme, str):
                readme_excerpt = readme[:4_000]
            metadata = repo_ctx.get("metadata") or {}
            if isinstance(metadata, dict) and isinstance(metadata.get("description"), str):
                repo_description = metadata["description"]

    agent = OrgResolverAgent()
    warnings: list[str] = []

    for org in candidates:
        org_id = org.get("id") or org.get("@id") or "?"
        name = org.get("schema:name") or "?"
        unresolved = UnresolvedOrg(
            org_id=str(org_id),
            **{"schema:name": str(name)},
            **{"pulse:OrganizationType": org.get("pulse:OrganizationType")},
            affiliated_person_names=_affiliated_person_names(
                org_id, persons, memberships,
            ),
        )
        # Silver-bullet pre-fetch: fan the org name out to Infoscience
        # (exact code lookup), GitHub (handle resolution), ROR (composite
        # parent/unit), and the federated RAG. Saves the LLM from
        # discovering the right tool route on its own and prevents the
        # 25-call request budget from being burned on noisy semantic
        # searches when an exact lookup would have answered in one hop.
        pre_fetched = await _gather_federated_evidence(
            str(name), providers=providers,
        )
        try:
            patch = await agent.run(
                refiner_input=OrgResolverInput(
                    repo_handle=repo_handle,
                    repo_description=repo_description,
                    readme_excerpt=readme_excerpt,
                    org=unresolved,
                    pre_fetched_evidence=pre_fetched,
                ),
                tools=tools,
            )
        except LLMRuntimeError as exc:
            warnings.append(
                f"org_resolver: failed for {name!r} — {exc}",
            )
            stats["failed"] += 1
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("org_resolver crashed for %s", name)
            warnings.append(
                f"org_resolver: crashed for {name!r} — {exc}",
            )
            stats["failed"] += 1
            continue

        if _apply_org_resolver_patch(org, patch):
            stats["resolved"] += 1
            warnings.append(
                f"org_resolver: resolved {name!r} (confidence={patch.confidence:.2f}, "
                f"reason={(patch.reason or '')[:120]!r})",
            )
        else:
            stats["declined"] += 1
            warnings.append(
                f"org_resolver: declined {name!r} (no tool result cleared the bar)",
            )

    return (warnings, stats)


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

    logger.info(
        "rescue_refiner: payload — repo=%s readme=%s cff=%s aux_files=%s candidates=%d",
        repo_handle,
        f"{len(readme_text)}B" if readme_text else "none",
        f"{len(citation_cff)}B" if citation_cff else "none",
        {k: f"{len(v)}B" for k, v in aux_files.items()},
        len(candidates),
    )
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
            # `schema:identifier` is the catch-all required by the
            # OrganizationShape's `sh:or` branch when no ROR / handle /
            # infoscience id is available. When the rescue candidate's
            # org_id is just a UUID (no recognisable scheme), fall back
            # to the org name as the identifier so the stub clears
            # strict validation. Otherwise the rescued Membership ends
            # up pointing at an Org that gets SHACL-dropped, which then
            # drops the Person too.
            schema_identifier = (
                org_id if org_id.startswith("https://ror.org/")
                else org_name if id_source == "uuid"
                else None
            )
            org_stub = {
                "id": org_id,
                "type": "org:Organization",
                "shacl": "pulse:OrganizationShape",
                "identifiers": {
                    # SHACL-required: every Org carries a uuid alongside its
                    # primary external identifier (mirrors reconciliation).
                    "uuid": str(uuid4()),
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
                "schema:identifier": schema_identifier,
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
                    # SHACL-required: every Membership carries a uuid.
                    "uuid": str(uuid4()),
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
        # Schema requires plain string membership IDs in `org:hasMembership`
        # (rule-based reconciliation emits them that way). Earlier this
        # pushed `{"@id": ...}` dicts which tripped strict validation and
        # dropped the parent Person from the output entirely.
        membership_id = membership["id"]
        existing_id_set = {
            r.get("@id") if isinstance(r, dict) else r for r in existing_refs
        }
        if membership_id not in existing_id_set:
            existing_refs.append(membership_id)

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

    # Indexes built once for cross-identifier dedup + lab-Org parent
    # inference. `person_dedup_index` catches the case where the same
    # human already exists under a different @id (ORCID vs github);
    # `memberships` lets us discover which institution a lab-flavoured
    # handle should be `org:unitOf`.
    person_dedup_index = _build_person_dedup_index(persons)
    existing_memberships = reconciled.memberships or []

    for p in proposal.new_persons[:DISCOVERY_REPLY_MAX_PER_TYPE]:
        handle_candidate = _normalize_github_handle(p.pulse_githubUsername)
        if handle_candidate and _looks_lab_flavored(handle_candidate):
            # The AUTHORS line names a person but the handle is a lab
            # identity — materialise as Org with `org:unitOf` toward the
            # institution where the named person already holds a
            # Membership (when we can identify it confidently).
            parent_org_id = _infer_lab_parent_org(
                p.schema_name, persons, existing_memberships,
            )
            entity, warning = _materialize_lab_org(
                p, existing_org_ids, parent_org_id,
            )
            _accept(entity, warning, org_bucket, existing_org_ids)
            # Stamp the reciprocal `org:hasUnit` on the parent so the
            # downstream `prune_dangling_refs` pass doesn't drop the
            # new lab Org as an orphan (it only counts INCOMING refs,
            # and `org:unitOf` is outgoing). Without this the lab
            # disappears from the final graph.
            if entity is not None and parent_org_id:
                for parent_entity in org_bucket:
                    if not isinstance(parent_entity, dict):
                        continue
                    if parent_entity.get("id") != parent_org_id:
                        continue
                    has_units = parent_entity.get("org:hasUnit")
                    if not isinstance(has_units, list):
                        has_units = []
                        parent_entity["org:hasUnit"] = has_units
                    if entity["id"] not in has_units:
                        has_units.append(entity["id"])
                    break
        else:
            entity, warning = _materialize_person(
                p, existing_person_ids, person_dedup_index,
            )
            _accept(entity, warning, person_bucket, existing_person_ids)
    for o in proposal.new_orgs[:DISCOVERY_REPLY_MAX_PER_TYPE]:
        e, w = _materialize_org(o, existing_org_ids)
        _accept(e, w, org_bucket, existing_org_ids)
    for a in proposal.new_articles[:DISCOVERY_REPLY_MAX_PER_TYPE]:
        # _materialize_article does a blocking OpenAlex DOI lookup
        # (_openalex_lookup_doi → requests.get, 15s) — offload it so it doesn't
        # freeze the event loop during a hybrid extraction (Bug 02). Sequential
        # await keeps the shared dedup dicts race-free.
        article, new_persons, warning = await asyncio.to_thread(
            _materialize_article,
            a, existing_article_ids, existing_person_ids, person_dedup_index,
        )
        _accept(article, warning, article_bucket, existing_article_ids)
        # OpenAlex-discovered authors that aren't in the graph yet need
        # to be added as Person stubs alongside the article, otherwise
        # `schema:author` refs dangle and `prune_dangling_refs` removes
        # the article entirely.
        for person_stub in new_persons:
            if person_stub["id"] in existing_person_ids:
                continue
            person_bucket.append(person_stub)
            existing_person_ids.add(person_stub["id"])
            stats["added"] += 1
            warnings.append(
                f"discovery_refiner: added schema:Person {person_stub['id']} "
                f"(OpenAlex authorship of {a.schema_identifier!r})",
            )
            # Keep the dedup index in sync so later proposals don't
            # re-propose this same author.
            handle = person_stub.get("pulse:githubUsername")
            if isinstance(handle, str) and handle:
                person_dedup_index.setdefault(
                    f"gh:{handle.lower()}", person_stub["id"],
                )
            orcid_url = person_stub.get("pulse:orcidIdentifier")
            if isinstance(orcid_url, str):
                orcid_match = re.search(
                    r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", orcid_url, re.IGNORECASE,
                )
                if orcid_match:
                    person_dedup_index.setdefault(
                        f"orcid:{orcid_match.group(1).upper()}",
                        person_stub["id"],
                    )

    # Write back only when something was added.
    if stats["added"]:
        reconciled.entities["persons"] = person_bucket
        reconciled.entities["organizations"] = org_bucket
        reconciled.entities["articles"] = article_bucket

    return (warnings, stats)


# ---------------------------------------------------------------------------
# Repo-signals LLM pass (Phase 2 README enrichment)
# ---------------------------------------------------------------------------

_REPO_SIGNALS_AGENT_MODE_ENV = "V2_REPO_SIGNALS_AGENT_MODE"


def _repo_signals_agent_mode() -> str:
    """Return the gated mode for the repo-signals LLM pass.

    Values: ``apply`` (default), ``shadow``, ``off``.  Unknown values fall
    back to ``apply``.
    """
    raw = (os.getenv(_REPO_SIGNALS_AGENT_MODE_ENV) or "apply").strip().lower()
    return raw if raw in {"apply", "shadow", "off"} else "apply"


async def _run_repo_signals_pass(  # noqa: C901, PLR0912
    *,
    repositories: list[dict[str, Any]],
    gathered_context: dict[str, Any] | None,
) -> list[str]:
    """Run the LLM repo-signals refiner over every repository entity.

    Mutates each repository entity **in place** when mode is ``apply``:
    - Sets ``_documentation_urls`` to the de-duped list the LLM confirmed.
    - Sets ``_test_coverage`` **only** when the entity's current value is
      ``None`` (the deterministic Phase-1 regex result wins; LLM is a
      fallback only).

    In ``shadow`` mode the agent runs but the entity is left unchanged;
    its proposed values are logged.

    In ``off`` mode the agent is never called.

    Returns a list of human-readable warning/log strings.
    """
    mode = _repo_signals_agent_mode()
    if mode == "off" or not repositories:
        return []

    # Extract the README from gathered_context (same path as _build_repo_context_summary).
    readme: str | None = None
    if isinstance(gathered_context, dict):
        repo_ctx = gathered_context.get("repository")
        if isinstance(repo_ctx, dict):
            readme_raw = repo_ctx.get("readme_content")
            if isinstance(readme_raw, str) and readme_raw.strip():
                readme = readme_raw

    if not readme:
        return []

    readme_excerpt = readme[:README_CONTEXT_MAX_CHARS]
    candidate_urls = extract_doc_candidate_urls(readme)

    warnings: list[str] = []

    for repo in repositories:
        if not isinstance(repo, dict):
            continue
        handle = (
            repo.get("pulse:githubRepositoryHandle")
            or repo.get("schema:name")
            or repo.get("id", "?")
        )

        refiner_input = RepoSignalsInput(
            repository_handle=str(handle),
            readme_excerpt=readme_excerpt,
            candidate_documentation_urls=candidate_urls,
        )

        try:
            patch = await run_repo_signals(refiner_input)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"repo_signals: unexpected error for {handle!r} — {exc}; skipping",
            )
            continue

        if patch is None:
            warnings.append(f"repo_signals: no patch returned for {handle!r}")
            continue

        if mode == "shadow":
            warnings.append(
                f"repo_signals [shadow] {handle!r}: would set "
                f"_documentation_urls={patch.documentation_urls!r} "
                f"_test_coverage={patch.test_coverage!r}",
            )
            continue

        # apply mode — mutate the entity
        if patch.documentation_urls:
            # De-duped union: the agent's list is authoritative (it may have
            # confirmed a subset of candidates + added new ones).
            seen: set[str] = set()
            deduped: list[str] = []
            for url in patch.documentation_urls:
                if url not in seen:
                    seen.add(url)
                    deduped.append(url)
            repo["_documentation_urls"] = deduped
            warnings.append(
                f"repo_signals: set _documentation_urls={deduped!r} for {handle!r}",
            )

        if patch.test_coverage is not None and repo.get("_test_coverage") is None:
            repo["_test_coverage"] = patch.test_coverage
            warnings.append(
                f"repo_signals: set _test_coverage={patch.test_coverage!r} "
                f"(LLM fallback) for {handle!r}",
            )

    return warnings


async def run_refine_with_llm_stage(  # noqa: PLR0913, PLR0915
    *,
    reconciled: ReconciledEntities,
    gathered_context: dict[str, Any] | None,
    org_refiner: OrganizationRefinerAgent | None = None,
    repo_refiner: RepositoryRefinerAgent | None = None,
    person_refiner: PersonRefinerAgent | None = None,
    membership_refiner: MembershipRefinerAgent | None = None,
    epfl_graph_provider: EpflGraphRagProvider | None = None,
    providers: Any | None = None,
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

    # Org resolver pass: anchor un-identified orgs by routing the LLM
    # through ROR / Infoscience / EPFL Graph / GitHub. Runs BEFORE
    # rescue so any newly anchored orgs (e.g. UPMWMATHIS → Infoscience
    # id, @AdaptiveMotorControlLab → GitHub org metadata) can serve as
    # valid Membership targets when rescue re-evaluates dropped pairs.
    resolver_warnings, resolver_stats = await _run_org_resolver_pass(
        reconciled=reconciled,
        gathered_context=gathered_context,
        providers=providers,
    )
    warnings.extend(resolver_warnings)
    by_type["org_resolver"] = resolver_stats

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

    # Repo-signals pass (Phase 2): LLM confirms/extends documentation URLs
    # and provides a test-coverage fallback when the deterministic regex
    # found nothing.  Env-gated by V2_REPO_SIGNALS_AGENT_MODE
    # (apply / shadow / off); default is apply.
    repo_signals_warnings = await _run_repo_signals_pass(
        repositories=repositories,
        gathered_context=gathered_context,
    )
    warnings.extend(repo_signals_warnings)

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
