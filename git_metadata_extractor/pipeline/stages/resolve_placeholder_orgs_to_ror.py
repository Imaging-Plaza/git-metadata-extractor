"""Stage: late re-resolution pass for UUID-placeholder Organizations.

The earlier resolver stages (8b/c/d) turn Person-side affiliation
strings (`_company`, `_bio`, `_orcid_biography`, `_blog`, `_email`)
into proper `org:Membership` + `org:Organization` entities pointing at
ROR ids. But Memberships also enter the graph from a *different* path:
the rule-based / LLM organization agents themselves, the LLM
membership agent, and the refine_with_llm rescue refiners. When an
agent sees an affiliation string but can't anchor it to a ROR /
Infoscience / GitHub id at extraction time, it emits an Organization
entity with `idSource = "uuid"` — the JSON-LD layer prefixes that
UUID with `urn:pulse:` at output time, producing the
`urn:pulse:<uuid>` placeholder bucket the operator sees in the SPARQL
store.

These placeholders carry the **original affiliation string** as
`schema:name`. That's the breadcrumb this stage uses: re-query the ROR
RAG against `schema:name`, and when a hit clears the same strict
acceptance gate the company/bio stages use, rewrite the Organization
in place AND patch every Membership that refers to its old UUID id.

What we DON'T do here:

  - Drop the placeholder. If the ROR lookup fails, the Organization
    stays as-is with `idSource = "uuid"` — downstream may want it for
    SPARQL queries that look at affiliation strings even when no ROR
    is known.
  - Cascade resolutions. Resolving X to ROR_X doesn't unblock any
    other placeholder, so we run a single pass; no while-loop needed.
  - Touch Organizations the resolver stages already minted with
    `idSource = "pulse:ror"`. Those are already canonical.

Idempotent across re-runs: an Org that gets rewritten this pass has
`idSource = "pulse:ror"` next pass and is no longer in the candidate
set. An Org that fails to resolve this pass is still a candidate next
pass (in case ROR has added the org between extracts).

Provenance: rewritten Orgs and patched Memberships get
``_source = "resolve_placeholder_orgs_to_ror"`` (stripped at output
unless ``include_internal_fields=true``).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.providers.ror_rag import RorRagProvider, build_default_provider
from git_metadata_extractor.pipeline.stages.resolve_company_to_ror import (
    _resolve_one,
    _strip_country,
)

if TYPE_CHECKING:
    from git_metadata_extractor.pipeline.stages.reconciliation import ReconciledEntities

logger = logging.getLogger(__name__)

STAGE_SOURCE_TAG = "resolve_placeholder_orgs_to_ror"


@dataclass(slots=True)
class PlaceholderResolutionResult:
    """Summary of one stage run."""

    placeholders_examined: int
    placeholders_resolved: int
    memberships_rewritten: int
    queries_attempted: int
    queries_accepted: int
    rejection_reasons: dict[str, int] = field(default_factory=dict)


def _placeholder_orgs(reconciled: "ReconciledEntities") -> list[dict[str, Any]]:
    """Return the Organization entities that are UUID-anchored and
    carry a non-empty ``schema:name`` we can re-query against."""
    out: list[dict[str, Any]] = []
    for org in reconciled.entities.get("organizations") or []:
        if not isinstance(org, dict):
            continue
        if org.get("idSource") != "uuid":
            continue
        name = org.get("schema:name")
        if not isinstance(name, str) or not name.strip():
            continue
        out.append(org)
    return out


def _rewrite_org_in_place(
    org: dict[str, Any], *, ror_id: str, ror_name: str | None,
) -> str:
    """Mutate ``org`` to point at the canonical ROR record. Returns
    the OLD id so the caller can patch Memberships that reference it.
    """
    old_id = str(org.get("id") or org.get("@id") or "")
    org["id"] = ror_id
    if "@id" in org:
        org["@id"] = ror_id
    org["idSource"] = "pulse:ror"
    identifiers = org.get("identifiers")
    if not isinstance(identifiers, dict):
        identifiers = {}
    identifiers["pulse:ror"] = ror_id
    # Drop the dangling uuid identifier — it's no longer the canonical
    # id, and leaving it can confuse downstream consumers that look for
    # `identifiers.uuid` as a fallback dedupe key.
    identifiers.pop("uuid", None)
    org["identifiers"] = identifiers
    # Prefer the canonical ROR display name when we have it; keep the
    # original placeholder string under `gme-internal:original_name`
    # so the breadcrumb is recoverable with `?include_internal_fields=true`.
    if ror_name:
        original = org.get("schema:name")
        if isinstance(original, str) and original.strip() and original != ror_name:
            org["_original_name"] = original
        org["schema:name"] = ror_name
    org["_source"] = STAGE_SOURCE_TAG
    return old_id


def _rewrite_memberships(
    memberships: list[dict[str, Any]],
    *,
    rewrites: dict[str, str],
) -> int:
    """For every Membership whose `org:organization` is in ``rewrites``,
    swap the reference and rebuild the composite id. Returns the count
    of memberships that changed.

    Idempotent: a second pass on the same `rewrites` map is a no-op
    because every match in the first pass already points at the
    canonical id, which isn't a key in `rewrites`.
    """
    if not rewrites:
        return 0
    n = 0
    for m in memberships:
        if not isinstance(m, dict):
            continue
        org_ref = m.get("org:organization")
        if not isinstance(org_ref, str) or org_ref not in rewrites:
            continue
        new_org = rewrites[org_ref]
        m["org:organization"] = new_org
        # Composite id: `personId__orgId` — rebuild the org half. The
        # split-on-first-`__` is safe because canonical Person IDs
        # don't contain `__`.
        old_composite = m.get("id")
        if isinstance(old_composite, str) and "__" in old_composite:
            person_part, _, _ = old_composite.partition("__")
            new_composite = f"{person_part}__{new_org}"
            m["id"] = new_composite
            identifiers = m.get("identifiers")
            if isinstance(identifiers, dict):
                identifiers["pulse:composite"] = new_composite
        m["_source"] = STAGE_SOURCE_TAG
        n += 1
    return n


async def run_resolve_placeholder_orgs_to_ror_stage(
    *,
    reconciled: "ReconciledEntities",
    provider: RorRagProvider | None = None,
) -> PlaceholderResolutionResult:
    """Stage entry. Walks placeholder Orgs (idSource=uuid + schema:name
    present), queries ROR, rewrites successful hits + their referring
    Memberships in place. Idempotent across re-runs."""
    provider = provider or build_default_provider()
    if provider is None:
        logger.warning(
            "resolve_placeholder_orgs_to_ror: RorRagProvider unavailable "
            "(check V2_ROR_RAG_ENABLED + INDEX_QDRANT_URL); stage skipped",
        )
        return PlaceholderResolutionResult(
            placeholders_examined=0,
            placeholders_resolved=0,
            memberships_rewritten=0,
            queries_attempted=0,
            queries_accepted=0,
            rejection_reasons={"provider_unavailable": 1},
        )

    placeholders = _placeholder_orgs(reconciled)
    if not placeholders:
        return PlaceholderResolutionResult(
            placeholders_examined=0,
            placeholders_resolved=0,
            memberships_rewritten=0,
            queries_attempted=0,
            queries_accepted=0,
        )

    cache: dict[str, Any] = {}
    reasons: dict[str, int] = {}
    # Old uuid-id → new ROR id, accumulated across all placeholders so
    # we can rewrite Memberships in one pass at the end.
    rewrites: dict[str, str] = {}
    resolved = 0

    for org in placeholders:
        raw_name = str(org.get("schema:name") or "")
        hit, reason = await _resolve_one(provider, cache, raw_name)
        if hit is None:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        ror_id = hit.get("ror_id")
        if not isinstance(ror_id, str) or not ror_id:
            continue
        ror_display = _strip_country(str(hit.get("name") or "")) or None
        old_id = _rewrite_org_in_place(org, ror_id=ror_id, ror_name=ror_display)
        if old_id and old_id != ror_id:
            rewrites[old_id] = ror_id
        resolved += 1

    memberships = reconciled.entities.get("memberships") or []
    memberships_rewritten = _rewrite_memberships(memberships, rewrites=rewrites)

    result = PlaceholderResolutionResult(
        placeholders_examined=len(placeholders),
        placeholders_resolved=resolved,
        memberships_rewritten=memberships_rewritten,
        queries_attempted=len(cache),
        queries_accepted=sum(1 for v in cache.values() if v is not None),
        rejection_reasons=reasons,
    )
    logger.info(
        "resolve_placeholder_orgs_to_ror: examined=%d resolved=%d "
        "memberships_rewritten=%d queries=%d accepted=%d",
        result.placeholders_examined,
        result.placeholders_resolved,
        result.memberships_rewritten,
        result.queries_attempted,
        result.queries_accepted,
    )
    return result


# Synchronous facade for offline callers (matches the convention of
# the other resolver stages).
def run_resolve_placeholder_orgs_to_ror_stage_sync(
    *,
    reconciled: "ReconciledEntities",
    provider: RorRagProvider | None = None,
) -> PlaceholderResolutionResult:
    return asyncio.run(
        run_resolve_placeholder_orgs_to_ror_stage(
            reconciled=reconciled, provider=provider,
        ),
    )


__all__ = [
    "PlaceholderResolutionResult",
    "STAGE_SOURCE_TAG",
    "run_resolve_placeholder_orgs_to_ror_stage",
    "run_resolve_placeholder_orgs_to_ror_stage_sync",
]
