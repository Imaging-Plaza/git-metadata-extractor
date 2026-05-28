"""Stage B of the affiliation-from-richer-Person-signals track.

Stage A (`resolve_bio_to_ror`) is purely deterministic — regex
extraction over bio / orcid bio + a domain-hints table over blog /
email. That covers every easy case but, by design, returns no-op on
prose that doesn't match its hand-written patterns:

  "Currently a Senior Software Engineer Manager working in Identity"
   (no `at <Org>`, no comma-after-role, no `from <Org>` — but a
   reasonable LLM call to ROR would resolve `Identity` ↔ a company
   given the bio's full text + the user's location.)

  "Building distributed systems for the next-gen LIGO collaboration"
   (LIGO is named verbatim, but the regex requires an adjacent
   role/preposition that this sentence doesn't have.)

  GitHub profile READMEs (`_profile_readme`) where the affiliation
  lives 800 chars into the markdown — way outside the regex window.

This stage closes the long tail. For every person STILL missing
`schema:affiliation` after Stage A, we call a single-person LLM
("BioResolverAgent") with `search_ror_rag` tool access. The acceptance
gate matches Stage A's: top-1 ROR hit must clear the same score / type
bar, AND the LLM must report `confidence >= 0.7`, AND the LLM must
quote a verbatim substring from the source text in `reason`.

Cost-control levers:

  - Only runs under the LLM / hybrid runtime (gated in `api.py`).
  - Skips persons that already have `schema:affiliation` from any
    prior stage (rule-based reconciliation, Stage A, or a hand-set
    value).
  - Skips persons with no extractable text at all (no bio, no
    orcid bio, no readme).
  - Bounded concurrency (default 4, env-tunable) so a single repo
    with 100 contributors doesn't fan out into 100 simultaneous LLM
    calls.
  - LLM call timeout (180 s default) per person — a hung call
    surfaces as a per-person warning, not a stage failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.v2.agents.llm.agent_tools.ror_rag import make_ror_rag_search_tool
from src.v2.agents.llm.refiners.bio_resolver import (
    CONFIDENCE_FLOOR,
    BioResolverAgent,
    BioResolverInput,
    BioResolverPatch,
    UnresolvedPerson,
)
from src.v2.agents.llm.runtime import LLMRuntimeError
from src.v2.ingest.providers.ror_rag import RorRagProvider
from src.v2.pipeline.stages.resolve_bio_to_ror import (
    BIO_KEYS,
    BLOG_KEYS,
    EMAIL_KEYS,
    ORCID_BIO_KEYS,
    _persons_with_memberships,
    _read_first,
)
from src.v2.pipeline.stages.resolve_company_to_ror import (
    _build_membership,
    _build_org_stub,
    _existing_membership_keys,
    _existing_org_ids,
    _person_canonical_id,
)

STAGE_SOURCE_TAG = "resolve_bio_to_ror_llm"

if TYPE_CHECKING:
    from src.v2.pipeline.stages.reconciliation import ReconciledEntities

logger = logging.getLogger(__name__)


# Fields whose multi-key shape we read for context. The Stage-A keys
# are imported as-is; profile-readme / location / orcid-country are
# new here.
def _multi_key(field_name: str) -> tuple[str, ...]:
    return (
        f"_{field_name}",
        f"gme-internal:{field_name}",
        f"https://openpulse.science/git-metadata-extractor#{field_name}",
    )


PROFILE_README_KEYS = _multi_key("profile_readme")
LOCATION_KEYS = _multi_key("location")
ORCID_COUNTRY_KEYS = _multi_key("orcid_country")
NAME_KEYS = ("schema:name", "name")

DEFAULT_MAX_CONCURRENCY = 4
MAX_CONCURRENCY_ENV = "V2_RESOLVE_BIO_TO_ROR_LLM_CONCURRENCY"


def _resolve_max_concurrency() -> int:
    raw = os.getenv(MAX_CONCURRENCY_ENV)
    if not raw:
        return DEFAULT_MAX_CONCURRENCY
    try:
        n = int(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not an int — falling back to %d",
            MAX_CONCURRENCY_ENV,
            raw,
            DEFAULT_MAX_CONCURRENCY,
        )
        return DEFAULT_MAX_CONCURRENCY
    return max(1, n)


@dataclass(slots=True)
class BioLLMAffiliationResult:
    """Summary of one stage run."""

    persons_examined: int
    persons_called: int
    persons_resolved: int
    persons_failed: int
    memberships_created: int = 0
    organizations_created: int = 0
    warnings: list[str] = field(default_factory=list)


def _read_name(person: dict[str, Any]) -> str | None:
    for key in NAME_KEYS:
        value = person.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _has_extractable_text(person: dict[str, Any]) -> bool:
    """At least one of bio / orcid bio / readme must be non-empty for
    an LLM call to be worth the spend — otherwise the model has
    literally nothing to quote."""
    return bool(
        _read_first(person, BIO_KEYS)
        or _read_first(person, ORCID_BIO_KEYS)
        or _read_first(person, PROFILE_README_KEYS),
    )


async def _resolve_one_person(
    *,
    agent: BioResolverAgent,
    person: dict[str, Any],
    tools: list[object],
    semaphore: asyncio.Semaphore,
) -> tuple[BioResolverPatch | None, str | None]:
    """Build the agent input, call the LLM, return (patch_or_None, warning_or_None)."""
    person_id = (
        person.get("@id")
        or person.get("id")
        or _read_name(person)
        or "<unknown-person>"
    )
    unresolved = UnresolvedPerson(
        person_id=str(person_id),
        **{"schema:name": _read_name(person)},
        bio=_read_first(person, BIO_KEYS),
        orcid_biography=_read_first(person, ORCID_BIO_KEYS),
        profile_readme=_read_first(person, PROFILE_README_KEYS),
        location=_read_first(person, LOCATION_KEYS),
        orcid_country=_read_first(person, ORCID_COUNTRY_KEYS),
    )
    refiner_input = BioResolverInput(person=unresolved)
    async with semaphore:
        try:
            patch = await agent.run(refiner_input=refiner_input, tools=tools)
        except LLMRuntimeError as exc:
            return None, f"resolve_bio_to_ror_llm: {person_id}: {exc}"
        except Exception as exc:  # noqa: BLE001 — per-person isolation
            logger.exception("resolve_bio_to_ror_llm: %s failed", person_id)
            return None, f"resolve_bio_to_ror_llm: {person_id}: {exc}"
    return patch, None


def _apply_patch(
    *,
    person: dict[str, Any],
    patch: BioResolverPatch,
    reconciled: "ReconciledEntities",
    existing_org_ids: set[str],
    existing_membership_keys: set[str],
) -> tuple[int, int]:
    """Materialise the LLM patch as Membership + Org entities (when
    the confidence floor is cleared and the ROR isn't already linked
    to this person). Returns ``(memberships_added, organizations_added)``.

    The agent already enforces the floor and a verbatim quote on the
    `reason` field; the stage-side floor here is defensive — same as
    the company-stage's strict acceptance gate.
    """
    if not patch.pulse_ror or patch.confidence < CONFIDENCE_FLOOR:
        return 0, 0
    person_id = _person_canonical_id(person)
    if not person_id:
        return 0, 0
    composite = f"{person_id}__{patch.pulse_ror}"
    if composite in existing_membership_keys:
        return 0, 0

    orgs = reconciled.entities.setdefault("organizations", [])
    memberships = reconciled.entities.setdefault("memberships", [])
    o_added = 0
    if patch.pulse_ror not in existing_org_ids:
        # We don't have a ROR `name` from the LLM patch — `reason`
        # carries a verbatim bio quote that may or may not be the
        # canonical name. Use the ROR URL itself so the Org stub is
        # well-formed; downstream refiners can backfill `schema:name`.
        stub = _build_org_stub(
            {"ror_id": patch.pulse_ror, "name": patch.pulse_ror},
            source=STAGE_SOURCE_TAG,
        )
        if stub is not None:
            orgs.append(stub)
            existing_org_ids.add(patch.pulse_ror)
            o_added = 1

    memberships.append(
        _build_membership(
            person_id=person_id,
            org_id=patch.pulse_ror,
            source=STAGE_SOURCE_TAG,
        ),
    )
    existing_membership_keys.add(composite)
    return 1, o_added


async def run_resolve_bio_to_ror_llm_stage(
    *,
    reconciled: "ReconciledEntities",
    provider: RorRagProvider | None = None,
    agent: BioResolverAgent | None = None,
    max_concurrency: int | None = None,
) -> BioLLMAffiliationResult:
    """Stage entry. For each Person without a Membership after Stage A
    that still has bio / orcid bio / readme text, call the LLM
    bio-resolver and (on a high-confidence patch) materialise a
    Membership + Organization entity. Idempotent across re-runs.
    """
    persons_raw = reconciled.entities.get("persons") or []
    affiliated_persons = _persons_with_memberships(reconciled)
    candidates: list[dict[str, Any]] = [
        p for p in persons_raw
        if isinstance(p, dict)
        and (_person_canonical_id(p) or "") not in affiliated_persons
        and _has_extractable_text(p)
    ]
    if not candidates:
        return BioLLMAffiliationResult(
            persons_examined=len(persons_raw),
            persons_called=0,
            persons_resolved=0,
            persons_failed=0,
        )

    if provider is None:
        logger.warning(
            "resolve_bio_to_ror_llm: RorRagProvider unavailable — stage skipped",
        )
        return BioLLMAffiliationResult(
            persons_examined=len(persons_raw),
            persons_called=0,
            persons_resolved=0,
            persons_failed=0,
            warnings=["resolve_bio_to_ror_llm: ROR provider unavailable"],
        )

    agent = agent or BioResolverAgent()
    tools: list[object] = [make_ror_rag_search_tool(provider)]
    sem = asyncio.Semaphore(max_concurrency or _resolve_max_concurrency())

    tasks = [
        _resolve_one_person(agent=agent, person=p, tools=tools, semaphore=sem)
        for p in candidates
    ]
    results = await asyncio.gather(*tasks)

    existing_org_ids = _existing_org_ids(reconciled)
    existing_membership_keys = _existing_membership_keys(reconciled)

    resolved = 0
    failed = 0
    memberships_added = 0
    organizations_added = 0
    warnings: list[str] = []
    for person, (patch, warning) in zip(candidates, results, strict=False):
        if warning:
            failed += 1
            warnings.append(warning)
            continue
        if patch is None:
            continue
        m_added, o_added = _apply_patch(
            person=person,
            patch=patch,
            reconciled=reconciled,
            existing_org_ids=existing_org_ids,
            existing_membership_keys=existing_membership_keys,
        )
        if m_added > 0:
            resolved += 1
            memberships_added += m_added
            organizations_added += o_added

    result = BioLLMAffiliationResult(
        persons_examined=len(persons_raw),
        persons_called=len(candidates),
        persons_resolved=resolved,
        persons_failed=failed,
        memberships_created=memberships_added,
        organizations_created=organizations_added,
        warnings=warnings,
    )
    logger.info(
        "resolve_bio_to_ror_llm: examined=%d called=%d resolved=%d failed=%d "
        "memberships=%d organizations=%d",
        result.persons_examined,
        result.persons_called,
        result.persons_resolved,
        result.persons_failed,
        result.memberships_created,
        result.organizations_created,
    )
    return result


__all__ = [
    "BIO_KEYS",
    "BLOG_KEYS",
    "EMAIL_KEYS",
    "LOCATION_KEYS",
    "NAME_KEYS",
    "ORCID_BIO_KEYS",
    "ORCID_COUNTRY_KEYS",
    "PROFILE_README_KEYS",
    "BioLLMAffiliationResult",
    "run_resolve_bio_to_ror_llm_stage",
]
