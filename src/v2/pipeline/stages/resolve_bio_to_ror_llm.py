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
    _read_first,
)
from src.v2.pipeline.stages.resolve_company_to_ror import SCHEMA_AFFILIATION

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


def _apply_patch(person: dict[str, Any], patch: BioResolverPatch) -> bool:
    """Stamp `schema:affiliation` on the person when the patch carries
    a high-confidence ROR. Returns True when something was written.

    Idempotent: if the ROR is already present (string or in a list)
    the call is a no-op."""
    if not patch.pulse_ror or patch.confidence < CONFIDENCE_FLOOR:
        return False
    new_ror = patch.pulse_ror
    existing = person.get(SCHEMA_AFFILIATION)
    if existing == new_ror:
        return False
    if isinstance(existing, list):
        if new_ror in existing:
            return False
        person[SCHEMA_AFFILIATION] = list(existing) + [new_ror]
    elif isinstance(existing, str):
        person[SCHEMA_AFFILIATION] = [existing, new_ror]
    else:
        person[SCHEMA_AFFILIATION] = new_ror
    return True


async def run_resolve_bio_to_ror_llm_stage(
    *,
    reconciled: "ReconciledEntities",
    provider: RorRagProvider | None = None,
    agent: BioResolverAgent | None = None,
    max_concurrency: int | None = None,
) -> BioLLMAffiliationResult:
    """Stage entry. For each Person still missing `schema:affiliation`
    after Stage A, run the LLM bio-resolver and stamp the affiliation
    when the LLM clears the confidence floor.

    Idempotent: re-running on already-stamped persons is a no-op.
    """
    persons_raw = reconciled.entities.get("persons") or []
    candidates: list[dict[str, Any]] = [
        p for p in persons_raw
        if isinstance(p, dict)
        and not p.get(SCHEMA_AFFILIATION)
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

    resolved = 0
    failed = 0
    warnings: list[str] = []
    for person, (patch, warning) in zip(candidates, results, strict=False):
        if warning:
            failed += 1
            warnings.append(warning)
            continue
        if patch is None:
            continue
        if _apply_patch(person, patch):
            resolved += 1

    result = BioLLMAffiliationResult(
        persons_examined=len(persons_raw),
        persons_called=len(candidates),
        persons_resolved=resolved,
        persons_failed=failed,
        warnings=warnings,
    )
    logger.info(
        "resolve_bio_to_ror_llm: examined=%d called=%d resolved=%d failed=%d",
        result.persons_examined,
        result.persons_called,
        result.persons_resolved,
        result.persons_failed,
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
