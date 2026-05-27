"""Stage: resolve gme-internal:company strings into ROR identifiers and
stamp ``schema:affiliation`` directly on the Person entity.

Why this stage exists
---------------------
``reconciliation.py`` correctly requires *either* role/dates *or* an
ORCID-on-Person + ROR-on-Org authority anchor before it materialises a
``Membership`` (this prevents the "Statistics Botswana"-class false
positives). The downstream ``refine_with_llm`` rescue pass can rescue
some dropped memberships, but only when the README/CITATION text
verbatim names the person + org.

That leaves a huge class of legitimate, evidence-free affiliations on
the floor: any GitHub user who writes ``company: Google`` (or any
variant — ``@google``, ``Google Inc.``, ``Google Brain``) and does not
also publish an ORCID iD ends up with **no** institutional link in the
graph, even though ROR confidently resolves the string.

This stage closes that gap by emitting ``schema:affiliation`` triples
(not Memberships — Memberships still require evidence per the existing
rule) when the GME's own ``search_ror`` skill returns a single high-
confidence research-org candidate. The output is intentionally
*weaker* than a Membership: ``schema:affiliation`` carries no role and
no dates, so downstream consumers that need provenance (validation,
critic pruning) can ignore it; consumers that only need org rollups
(dashboards, CHAOSS metrics) get the link they actually wanted.

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
match. Multi-word queries with ``--rerank`` would help — left as a
future knob.

Integration
-----------
The stage is meant to be invoked from ``api.py`` right after
``reconcile_entities`` and before the LLM critic / refiner. The
implementation is purely synchronous (vector search is fast: ~50 ms
per query on the GPU embedder), so it does not need the orchestrator
task machinery.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.v2.ingest.providers.ror_rag import RorRagProvider, build_default_provider

if TYPE_CHECKING:
    from src.v2.pipeline.stages.reconciliation import ReconciledEntities

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

# Schema IRIs (kept inline to avoid a pipeline-wide constants module).
SCHEMA_AFFILIATION = "http://schema.org/affiliation"
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


_COUNTRY_SUFFIX_RE = re.compile(r"\s*\([^)]+\)\s*$")
_HANDLE_RE = re.compile(r"^@\s*")
_TRAILING_PUNCT_RE = re.compile(r"[,.;:\-]+\s*$")
_WS_RE = re.compile(r"\s+")


@dataclass(slots=True)
class CompanyAffiliationResult:
    """Summary of one stage run, returned for logging / observability."""

    persons_examined: int
    persons_resolved: int
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
    cache: dict[str, str | None],
    raw_company: str,
) -> tuple[str | None, str]:
    """Resolve one raw company string. Caches by cleaned query so
    ``Google`` / ``@google`` / ``Google `` share a single search."""
    q = _clean_query(raw_company)
    if not q or q.lower() in NON_ORG_KEYS:
        return None, "non-org key"
    if q in cache:
        ror = cache[q]
        return ror, "cache hit" if ror else "cached negative"
    hits = await provider.search(query=q, scope_mode="worldwide", top_k=5, rerank=False)
    # Normalise hit dicts (provider may yield pydantic objects)
    norm: list[dict[str, Any]] = []
    for h in hits or []:
        if isinstance(h, dict):
            norm.append(h)
        elif hasattr(h, "model_dump"):
            norm.append(h.model_dump())
        else:
            norm.append(dict(h.__dict__))
    winner, reason = _accept(norm, q)
    ror_id = winner.get("ror_id") if winner else None
    cache[q] = ror_id
    return ror_id, reason


async def run_resolve_company_to_ror_stage(
    *,
    reconciled: "ReconciledEntities",
    provider: RorRagProvider | None = None,
) -> CompanyAffiliationResult:
    """Stage entry. Mutates each Person dict in ``reconciled.entities['persons']``
    by appending a ``schema:affiliation`` (str or list[str]) when a high-
    confidence ROR match is found.

    The function is idempotent — re-running on already-stamped persons
    will not duplicate existing affiliations and will not overwrite a
    pre-existing value that points at a different ROR.
    """
    provider = provider or build_default_provider()
    if provider is None:
        logger.warning(
            "resolve_company_to_ror: RorRagProvider unavailable (check "
            "V2_ROR_RAG_ENABLED + INDEX_QDRANT_URL); stage skipped",
        )
        return CompanyAffiliationResult(0, 0, 0, 0, {"provider_unavailable": 1})

    persons = reconciled.entities.get("persons") or []
    cache: dict[str, str | None] = {}
    reasons: dict[str, int] = {}
    resolved = 0

    for person in persons:
        if not isinstance(person, dict):
            continue
        raw_companies = _read_company(person)
        if not raw_companies:
            continue
        if isinstance(raw_companies, str):
            raw_list = [raw_companies]
        elif isinstance(raw_companies, list):
            raw_list = [c for c in raw_companies if isinstance(c, str)]
        else:
            continue

        # Each raw company string may carry joint affiliations.
        candidate_rors: list[str] = []
        for raw in raw_list:
            for part in _split_joint(raw):
                ror_id, reason = await _resolve_one(provider, cache, part)
                if ror_id:
                    if ror_id not in candidate_rors:
                        candidate_rors.append(ror_id)
                else:
                    reasons[reason] = reasons.get(reason, 0) + 1

        if not candidate_rors:
            continue

        # Merge with any pre-existing affiliation triple, dedupe.
        existing = person.get(SCHEMA_AFFILIATION)
        if isinstance(existing, str):
            merged = [existing] + [r for r in candidate_rors if r != existing]
        elif isinstance(existing, list):
            merged = list(existing) + [r for r in candidate_rors if r not in existing]
        else:
            merged = candidate_rors
        # Single-element values stay as strings (schema.org convention);
        # multi-element values become lists. JSON-LD output assembly
        # already handles both shapes.
        person[SCHEMA_AFFILIATION] = merged[0] if len(merged) == 1 else merged
        resolved += 1

    result = CompanyAffiliationResult(
        persons_examined=len(persons),
        persons_resolved=resolved,
        queries_attempted=len(cache),
        queries_accepted=sum(1 for v in cache.values() if v is not None),
        rejection_reasons=reasons,
    )
    logger.info(
        "resolve_company_to_ror: persons_examined=%d persons_resolved=%d "
        "queries=%d accepted=%d",
        result.persons_examined,
        result.persons_resolved,
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
