"""Stage: resolve ``_company`` strings to ROR ids and materialise the
result as proper ``org:Membership`` + ``org:Organization`` entities on
``reconciled.entities``. The Person itself is left alone — affiliation
without evidence is modelled as a Membership pointing to an Org, per
the v2 ontology. The stage does NOT emit a bare ``schema:affiliation``
triple on Person (that property isn't in the Open Pulse ontology).

Why this stage exists
---------------------
``reconciliation.py`` correctly requires *either* role/dates *or* an
ORCID-on-Person + ROR-on-Org authority anchor before it materialises a
``Membership`` from raw agent output (this prevents the "Statistics
Botswana"-class false positives that bit us in v1). The downstream
``refine_with_llm`` rescue pass can rescue some dropped memberships,
but only when the README/CITATION text verbatim names the person + org.

That leaves a huge class of legitimate, evidence-free affiliations on
the floor: any GitHub user who writes ``company: Google`` (or any
variant — ``@google``, ``Google Inc.``, ``Google Brain``) and does not
also publish an ORCID iD ends up with **no** institutional link in the
graph, even though ROR confidently resolves the string.

This stage closes that gap by materialising Memberships when the GME's
own ``search_ror`` skill returns a single high-confidence research-org
candidate. The Memberships carry no ``org:role`` and no dates — that's
honest (we have no evidence), and downstream consumers that need
provenance can read ``_source`` (internal-only, stripped at output by
default).

Decision rule
-------------
Accept the top-1 ROR hit when ALL of:
  - top-1 score >= ``MIN_SCORE`` (default 0.55), AND
  - top-1 types intersect ``ACCEPTED_TYPES`` (company / education /
    funder / facility / government / nonprofit), AND
  - EITHER (a) score gap to top-2 >= ``MIN_GAP`` (default 0.02),
    OR (b) top-1's name with the country qualifier stripped matches
    the query string exactly (the "OpenAI / ETH Zurich override" —
    rescues canonical orgs surrounded by siblings).

Reject every query whose canonical form is in ``NON_ORG_KEYS``
(``freelance``, ``self``, ``https``, …) before issuing a search.

The cross-encoder reranker is **off by default**. For single-word
company queries it hurts: the reranker over-weights semantic
similarity and promotes Calico / GamesThatWork over the literal
match.

Output shape
------------
For each accepted resolution the stage pushes:

  * one minimal Organization entity to ``reconciled.entities['organizations']``
    (id = ROR URL, type = ``org:Organization``, idSource = ``pulse:ror``,
    schema:name = ROR's display name) when the ROR URL isn't already
    represented in the graph;
  * one Membership entity to ``reconciled.entities['memberships']``
    (id = ``{personId}__{rorURL}``, type = ``org:Membership``,
    org:organization = ROR URL, org:role / time:hasBeginning /
    time:hasEnd = None) when the same composite isn't already present.

Both writes are idempotent — re-running the stage on a graph that
already contains its earlier output is a no-op.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.agents.models import generate_uuid
from git_metadata_extractor.canonicalization.github import github_org_iri
from git_metadata_extractor.providers.ror_rag import RorRagProvider, build_default_provider

if TYPE_CHECKING:
    from git_metadata_extractor.pipeline.stages.reconciliation import ReconciledEntities

logger = logging.getLogger(__name__)


# Tunable thresholds — exposed as module constants so they can be
# overridden via env vars in a follow-up commit if needed.
MIN_SCORE: float = 0.55
MIN_GAP: float = 0.02
ACCEPTED_TYPES: frozenset[str] = frozenset(
    {"company", "education", "funder", "facility", "government", "nonprofit"},
)
NON_ORG_KEYS: frozenset[str] = frozenset({
    "freelance", "self-employed", "independent", "self",
    "remote", "home", "unemployed", "retired",
    "various", "multiple", "https",
})

# Source tag stamped on `_source` for every Membership/Org this stage
# generates, so downstream consumers (the critic, refine_with_llm,
# the SHACL gate's logs) can distinguish resolver output from
# text-extracted entities. Internal-only — `_drop_internal_keys` strips
# it before strict validation + JSON-LD output by default.
STAGE_SOURCE_TAG = "resolve_company_to_ror"

# The Person dict carries the company under different keys depending on
# where in the pipeline we run. In-pipeline (between reconciliation and
# the LLM critic) it's the rule-based agent's `_company`. The
# `gme-internal:` prefixed form lands later in `jsonld_build`. The
# full IRI is only present in post-hoc SPARQL queries against the
# already-expanded JSON-LD graph. We read whichever is set.
COMPANY_KEYS: tuple[str, ...] = (
    "_company",
    "gme-internal:company",
    "https://openpulse.science/git-metadata-extractor#company",
)
# Back-compat alias for callers that imported this name from the
# pre-fix version of the module.
GME_INTERNAL_COMPANY = COMPANY_KEYS[2]


def _read_company(person: dict[str, Any]) -> Any:
    """Return the first non-empty company value across every key shape
    the Person dict might carry it under (see ``COMPANY_KEYS``)."""
    for key in COMPANY_KEYS:
        value = person.get(key)
        if value:
            return value
    return None


# `_company` / `_bio` are free text that often name an affiliation with a
# GitHub `@handle` (e.g. company "@google-deepmind", bio "PhD @ucl, now @huggingface").
# An @handle is far more reliably an *organization* via a GitHub lookup than via
# free-text ROR search — and once linked as a github org it resolves to ROR
# through the web-domain cascade. We mine both fields for @handles and check
# each against GitHub.
BIO_KEYS: tuple[str, ...] = ("_bio", "_orcid_biography", "_profile_readme")
# GitHub handle grammar: alphanumeric + single hyphens, 1-39 chars.
_AT_HANDLE_RE = re.compile(r"@([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)")


def _extract_at_handles(person: dict[str, Any]) -> list[str]:
    """Collect distinct `@handle` mentions from the person's company + bio
    free-text fields, preserving first-seen order (lowercased)."""
    texts: list[str] = []
    company = _read_company(person)
    if isinstance(company, str):
        texts.append(company)
    elif isinstance(company, list):
        texts.extend(c for c in company if isinstance(c, str))
    for key in BIO_KEYS:
        value = person.get(key)
        if isinstance(value, str):
            texts.append(value)
    seen: set[str] = set()
    handles: list[str] = []
    for text in texts:
        for match in _AT_HANDLE_RE.finditer(text):
            handle = match.group(1).lower()
            if handle not in seen:
                seen.add(handle)
                handles.append(handle)
    return handles


def _github_org_iri_and_name(
    github_provider: Any,
    handle: str,
    cache: dict[str, tuple[str, str] | None],
) -> tuple[str, str] | None:
    """If `handle` is a GitHub *organization*, return its canonical handle IRI
    and display name; otherwise None. Cached per handle. Mirrors the
    `validate_org_github_handles` check (`get_user(...)['type'] == Organization`)."""
    if handle in cache:
        return cache[handle]
    result: tuple[str, str] | None = None
    try:
        data = github_provider.get_user(handle)
    except Exception:  # noqa: BLE001 — provider/network hiccup → treat as unknown
        data = None
    if isinstance(data, dict) and str(data.get("type", "")).lower() == "organization":
        iri = github_org_iri(handle) or f"https://github.com/{handle}"
        name = data.get("name") or data.get("login") or handle
        result = (iri, str(name))
    cache[handle] = result
    return result


def _build_github_org_stub(handle_iri: str, name: str, source: str) -> dict[str, Any]:
    """A github-org `org:Organization` stub (idSource = github handle). The
    web-domain cascade in `ownership_check` later anchors it to a ROR id."""
    return {
        "id": handle_iri,
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "identifiers": {
            "pulse:githubOrganizationHandle": handle_iri,
            "pulse:ror": None,
            "uuid": generate_uuid(),
        },
        "idSource": "pulse:githubOrganizationHandle",
        "schema:name": name,
        "pulse:githubOrganizationHandle": handle_iri,
        "_source": source,
    }


_COUNTRY_SUFFIX_RE = re.compile(r"\s*\([^)]+\)\s*$")
_HANDLE_RE = re.compile(r"^@\s*")
_TRAILING_PUNCT_RE = re.compile(r"[,.;:\-]+\s*$")
_WS_RE = re.compile(r"\s+")


@dataclass(slots=True)
class CompanyAffiliationResult:
    """Summary of one stage run, returned for logging / observability."""

    persons_examined: int
    persons_resolved: int
    memberships_created: int
    organizations_created: int
    queries_attempted: int
    queries_accepted: int
    rejection_reasons: dict[str, int]


def _clean_query(raw: str) -> str:
    """Strip ``@`` prefix, trailing punctuation, collapse whitespace."""
    s = _HANDLE_RE.sub("", raw).strip()
    s = _TRAILING_PUNCT_RE.sub("", s).strip()
    return _WS_RE.sub(" ", s)


def _split_joint(raw: str) -> list[str]:
    """Split joint affiliations (``X / Y``, ``A; B``, or
    ``NTU, UC Berkeley, @google`` when at least one chunk is an
    @-handle — for ``Google, Inc.`` the comma is a suffix delimiter
    and we keep it as one)."""
    parts = re.split(r"\s*[/;]\s*", raw)
    out: list[str] = []
    for part in parts:
        sub = [s.strip() for s in part.split(",") if s.strip()]
        if len(sub) > 1 and any(s.startswith("@") for s in sub):
            out.extend(sub)
        else:
            out.append(part.strip())
    return [s for s in out if s]


def _strip_country(name: str) -> str:
    return _COUNTRY_SUFFIX_RE.sub("", name or "").strip()


def _accept(hits: list[dict[str, Any]], query: str) -> tuple[dict | None, str]:
    """Return (winner_or_None, reason). See module docstring."""
    if not hits:
        return None, "no hits"
    top = hits[0]
    score = float(top.get("score", 0.0))
    if score < MIN_SCORE:
        return None, f"top score {score:.2f} < {MIN_SCORE}"
    types = {(t or "").lower() for t in (top.get("types") or [])}
    if not (types & ACCEPTED_TYPES):
        return None, f"top types {sorted(types)!r} not a research org"
    gap = score - float(hits[1].get("score", 0.0)) if len(hits) >= 2 else 1.0
    stripped = _strip_country(top.get("name", "")).lower()
    if gap >= MIN_GAP:
        return top, f"score {score:.2f} gap {gap:.2f}"
    if stripped == query.lower().strip():
        return top, f"score {score:.2f} gap {gap:.2f} exact-name override"
    return None, f"gap {gap:.2f} < {MIN_GAP} and no exact-name match"


async def _resolve_one(
    provider: RorRagProvider,
    cache: dict[str, dict[str, Any] | None],
    raw_company: str,
) -> tuple[dict[str, Any] | None, str]:
    """Resolve one raw company string. Caches by cleaned query so
    ``Google`` / ``@google`` / ``Google `` share a single search.

    Returns the winning ROR hit dict (with ``ror_id``, ``name``,
    ``types``) so callers can mint a matching Org stub without a
    second round trip. ``None`` when nothing clears the acceptance
    gate."""
    q = _clean_query(raw_company)
    if not q or q.lower() in NON_ORG_KEYS:
        return None, "non-org key"
    if q in cache:
        hit = cache[q]
        return hit, "cache hit" if hit else "cached negative"
    hits = await provider.search(query=q, scope_mode="worldwide", top_k=5, rerank=False)
    norm: list[dict[str, Any]] = []
    for h in hits or []:
        if isinstance(h, dict):
            norm.append(h)
        elif hasattr(h, "model_dump"):
            norm.append(h.model_dump())
        else:
            norm.append(dict(h.__dict__))
    winner, reason = _accept(norm, q)
    cache[q] = winner
    return winner, reason


# ---------------------------------------------------------------------------
# Membership / Organization materialisation
# ---------------------------------------------------------------------------


def _build_org_stub(hit: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    """Build a minimal Organization entity from a ROR hit. Returns
    ``None`` if the hit lacks a `ror_id`. Schema-conformant to
    ``pulse:OrganizationShape`` (id, type, shacl, identifiers, idSource,
    schema:name)."""
    ror_id = hit.get("ror_id")
    if not isinstance(ror_id, str) or not ror_id:
        return None
    name = _strip_country(str(hit.get("name") or "")) or ror_id
    return {
        "id": ror_id,
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "identifiers": {
            "pulse:ror": ror_id,
            "uuid": generate_uuid(),
        },
        "idSource": "pulse:ror",
        "schema:name": name,
        # Internal provenance — stripped at output by `_drop_internal_keys`
        # unless `?include_internal_fields=true`. Not in the ontology;
        # purely for tooling/debugging.
        "_source": source,
    }


def _build_membership(
    *, person_id: str, org_id: str, source: str,
) -> dict[str, Any]:
    """Build a Membership entity linking person→org. No role, no dates
    (we have no evidence). Schema-conformant to ``pulse:MembershipShape``."""
    composite_id = f"{person_id}__{org_id}"
    return {
        "id": composite_id,
        "type": "org:Membership",
        "shacl": "pulse:MembershipShape",
        "identifiers": {
            "pulse:composite": composite_id,
            "uuid": generate_uuid(),
        },
        "idSource": "pulse:composite",
        "org:organization": org_id,
        "org:role": None,
        "time:hasBeginning": None,
        "time:hasEnd": None,
        "_source": source,
    }


def _existing_org_ids(reconciled: "ReconciledEntities") -> set[str]:
    out: set[str] = set()
    for org in reconciled.entities.get("organizations") or []:
        if isinstance(org, dict):
            org_id = org.get("id")
            if isinstance(org_id, str):
                out.add(org_id)
    return out


def _existing_membership_keys(reconciled: "ReconciledEntities") -> set[str]:
    out: set[str] = set()
    for m in reconciled.entities.get("memberships") or []:
        if isinstance(m, dict):
            mid = m.get("id")
            if isinstance(mid, str):
                out.add(mid)
    return out


def _materialise(
    *,
    reconciled: "ReconciledEntities",
    person_id: str,
    hits: list[dict[str, Any]],
    source: str,
    existing_org_ids: set[str],
    existing_membership_keys: set[str],
) -> tuple[int, int]:
    """Push Org stubs + Memberships for each accepted ROR hit. Returns
    ``(memberships_added, organizations_added)``. Mutates ``reconciled``
    in place; updates the existing-id sets so within a single stage
    invocation later persons don't re-add the same org."""
    orgs = reconciled.entities.setdefault("organizations", [])
    memberships = reconciled.entities.setdefault("memberships", [])
    m_added = 0
    o_added = 0
    for hit in hits:
        ror_id = hit.get("ror_id")
        if not isinstance(ror_id, str) or not ror_id:
            continue
        if ror_id not in existing_org_ids:
            stub = _build_org_stub(hit, source=source)
            if stub is not None:
                orgs.append(stub)
                existing_org_ids.add(ror_id)
                o_added += 1
        composite = f"{person_id}__{ror_id}"
        if composite not in existing_membership_keys:
            memberships.append(
                _build_membership(person_id=person_id, org_id=ror_id, source=source),
            )
            existing_membership_keys.add(composite)
            m_added += 1
    return m_added, o_added


def _person_canonical_id(person: dict[str, Any]) -> str | None:
    """The membership composite needs a stable person id. Prefer the
    pipeline's `id` field, fall back to `@id`."""
    for key in ("id", "@id"):
        value = person.get(key)
        if isinstance(value, str) and value:
            return value
    return None


async def run_resolve_company_to_ror_stage(
    *,
    reconciled: "ReconciledEntities",
    provider: RorRagProvider | None = None,
    github_provider: Any = None,
) -> CompanyAffiliationResult:
    """Stage entry. Materialises one Membership + (when new) one
    Organization per accepted ROR hit, idempotently.

    When ``github_provider`` is supplied, `@handle` mentions in the person's
    ``_company`` / ``_bio`` that GitHub confirms are *organizations* are linked
    as github-org affiliations (and excluded from the free-text ROR search,
    which is far less reliable for a handle); the github org then resolves to
    ROR via the web-domain cascade in ``ownership_check``.

    Re-running the stage on a graph that already contains its earlier
    output is a no-op — both the org set and the membership composite
    set are pre-computed from `reconciled.entities` so duplicates can't
    creep in across re-runs.
    """
    provider = provider or build_default_provider()
    if provider is None:
        logger.warning(
            "resolve_company_to_ror: RorRagProvider unavailable (check "
            "V2_ROR_RAG_ENABLED + INDEX_QDRANT_URL); stage skipped",
        )
        return CompanyAffiliationResult(0, 0, 0, 0, 0, 0, {"provider_unavailable": 1})

    persons = reconciled.entities.get("persons") or []
    cache: dict[str, dict[str, Any] | None] = {}
    gh_cache: dict[str, tuple[str, str] | None] = {}
    reasons: dict[str, int] = {}
    resolved_persons: set[str] = set()
    memberships_added = 0
    organizations_added = 0

    existing_org_ids = _existing_org_ids(reconciled)
    existing_membership_keys = _existing_membership_keys(reconciled)

    for person in persons:
        if not isinstance(person, dict):
            continue
        person_id = _person_canonical_id(person)
        if not person_id:
            continue

        # --- GitHub @handle -> org affiliation (from _company + _bio) -------
        # A handle GitHub confirms is an organization is linked directly; record
        # it so the free-text ROR search below skips that chunk (an @handle is a
        # much weaker free-text ROR query than a confirmed github org).
        github_handles: set[str] = set()
        if github_provider is not None:
            for handle in _extract_at_handles(person):
                meta = _github_org_iri_and_name(github_provider, handle, gh_cache)
                if meta is None:
                    continue
                iri, name = meta
                github_handles.add(handle)
                if iri not in existing_org_ids:
                    reconciled.entities.setdefault("organizations", []).append(
                        _build_github_org_stub(iri, name, STAGE_SOURCE_TAG),
                    )
                    existing_org_ids.add(iri)
                    organizations_added += 1
                composite = f"{person_id}__{iri}"
                if composite not in existing_membership_keys:
                    reconciled.entities.setdefault("memberships", []).append(
                        _build_membership(
                            person_id=person_id, org_id=iri, source=STAGE_SOURCE_TAG,
                        ),
                    )
                    existing_membership_keys.add(composite)
                    memberships_added += 1
                    resolved_persons.add(person_id)

        raw_companies = _read_company(person)
        if not raw_companies:
            continue
        if isinstance(raw_companies, str):
            raw_list = [raw_companies]
        elif isinstance(raw_companies, list):
            raw_list = [c for c in raw_companies if isinstance(c, str)]
        else:
            continue

        candidate_hits: list[dict[str, Any]] = []
        for raw in raw_list:
            for part in _split_joint(raw):
                # Skip a chunk already linked as a github org above.
                if part.startswith("@") and _clean_query(part).lower() in github_handles:
                    continue
                hit, reason = await _resolve_one(provider, cache, part)
                if hit is not None:
                    ror_id = hit.get("ror_id")
                    if isinstance(ror_id, str) and ror_id and not any(
                        h.get("ror_id") == ror_id for h in candidate_hits
                    ):
                        candidate_hits.append(hit)
                else:
                    reasons[reason] = reasons.get(reason, 0) + 1

        if not candidate_hits:
            continue

        m_added, o_added = _materialise(
            reconciled=reconciled,
            person_id=person_id,
            hits=candidate_hits,
            source=STAGE_SOURCE_TAG,
            existing_org_ids=existing_org_ids,
            existing_membership_keys=existing_membership_keys,
        )
        if m_added > 0:
            resolved_persons.add(person_id)
            memberships_added += m_added
            organizations_added += o_added

    result = CompanyAffiliationResult(
        persons_examined=len(persons),
        persons_resolved=len(resolved_persons),
        memberships_created=memberships_added,
        organizations_created=organizations_added,
        queries_attempted=len(cache),
        queries_accepted=sum(1 for v in cache.values() if v is not None),
        rejection_reasons=reasons,
    )
    logger.info(
        "resolve_company_to_ror: persons_examined=%d persons_resolved=%d "
        "memberships_created=%d organizations_created=%d "
        "queries=%d accepted=%d",
        result.persons_examined,
        result.persons_resolved,
        result.memberships_created,
        result.organizations_created,
        result.queries_attempted,
        result.queries_accepted,
    )
    return result


# Synchronous facade for call sites that do not have an event loop
# (e.g. the unit-tests of older stages). The async version above is
# preferred — the orchestrator runs inside the FastAPI event loop.
def run_resolve_company_to_ror_stage_sync(
    *,
    reconciled: "ReconciledEntities",
    provider: RorRagProvider | None = None,
) -> CompanyAffiliationResult:
    return asyncio.run(
        run_resolve_company_to_ror_stage(reconciled=reconciled, provider=provider),
    )
