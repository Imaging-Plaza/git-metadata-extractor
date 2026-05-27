"""Stage A of the "infer affiliation from richer Person signals" track.

The first deterministic pass (`resolve_company_to_ror`) only reads the
single structured `_company` field. That misses a large class of
profiles where the affiliation lives in `_bio` (free text), in
`_orcid_biography` (ORCID's longer-form bio), or in `_blog` (a personal
website whose host already names the institution).

Concrete examples we saw on real profiles:

  github.com/joshabramson — bio: "Senior Research Engineer at Google DeepMind"
                            (`_company` empty)
  github.com/nikita-smetanin — bio explicit about employer, `_company`
                               sometimes empty or stale
  github.com/lyskov         — bio names lab + university affiliation,
                              `_company` is a personal handle

This stage:

  1. Pulls candidate org strings out of `_bio` and `_orcid_biography`
     using a small set of regexes:
       - ``at <Capitalized phrase>`` ("Research Scientist at DeepMind")
       - ``@<handle>`` ("@DeepMind", "@google")
       - ``, <Capitalized phrase>`` after a role
         ("PhD candidate, ETH Zurich")
       - ``from <Capitalized phrase>`` ("PhD from MIT")
  2. Maps `_blog` URLs through a small `DOMAIN_HINTS` table. We do NOT
     hardcode ROR ids — the table is `host → search query` and the
     same strict ROR resolver evaluates the hit. So a new
     domain entry doesn't need a ROR id lookup at code-edit time.
  3. Sends every candidate through the **same** `_resolve_one`
     resolver `resolve_company_to_ror` uses. So the acceptance gate
     (score ≥ 0.55, gap ≥ 0.02, org-type allowlist, exact-name
     override) is identical — no second decision rule to keep in sync.
  4. Stamps `schema:affiliation` (no role / no dates), matching the
     `resolve_company_to_ror` contract.

By default the stage **skips persons already affiliated** by an earlier
stage — it's a backstop for the `_company`-empty case, not a re-runner.
Call with ``skip_if_already_affiliated=False`` to additively enrich
already-affiliated persons (when bio surfaces a co-affiliation the
company field missed).

This stage is meant to run **right after** `resolve_company_to_ror` in
`api.py`, so the company-resolver cache and decision rule stay
authoritative for the easy case.

LLM stage B (per-person agent over the remaining `_bio` text) is a
separate stage, added later in the same pipeline slot when the
hybrid LLM runtime is enabled.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from src.v2.ingest.providers.ror_rag import RorRagProvider, build_default_provider
from src.v2.pipeline.stages.resolve_company_to_ror import (
    SCHEMA_AFFILIATION,
    _resolve_one,
)

if TYPE_CHECKING:
    from src.v2.pipeline.stages.reconciliation import ReconciledEntities

logger = logging.getLogger(__name__)


# Same multi-key fallback pattern as resolve_company_to_ror — the Person
# dict carries these fields under different keys depending on whether
# the stage runs in-pipeline (`_bio`) or post-jsonld-build
# (`gme-internal:bio`) or post-hoc SPARQL (full IRI).
def _multi_key(field: str) -> tuple[str, ...]:
    return (
        f"_{field}",
        f"gme-internal:{field}",
        f"https://openpulse.science/git-metadata-extractor#{field}",
    )


BIO_KEYS = _multi_key("bio")
ORCID_BIO_KEYS = _multi_key("orcid_biography")
BLOG_KEYS = _multi_key("blog")


# Host → ROR search query. Values are the institution NAME (not a ROR
# id) so the strict resolver still gates the match — a stale entry
# can't introduce a wrong ROR link, just an unhelpful query.
#
# Add more entries by appending one line. The blog host match is
# left-to-right suffix-aware (`lab.epfl.ch` finds `epfl.ch`), so
# subdomain entries are unnecessary.
DOMAIN_HINTS: dict[str, str] = {
    # EPFL ecosystem
    "epfl.ch": "EPFL",
    # ETH Zurich
    "ethz.ch": "ETH Zurich",
    "eth.ch": "ETH Zurich",
    # Swiss universities + research orgs
    "unige.ch": "University of Geneva",
    "unil.ch": "University of Lausanne",
    "unibe.ch": "University of Bern",
    "unibas.ch": "University of Basel",
    "uzh.ch": "University of Zurich",
    "usi.ch": "Università della Svizzera italiana",
    "psi.ch": "Paul Scherrer Institute",
    "cern.ch": "CERN",
    "empa.ch": "Empa",
    "eawag.ch": "Eawag",
    # US universities — common in the EPFL-heavy contributor pool
    "mit.edu": "Massachusetts Institute of Technology",
    "stanford.edu": "Stanford University",
    "berkeley.edu": "University of California, Berkeley",
    "cmu.edu": "Carnegie Mellon University",
    "harvard.edu": "Harvard University",
    "princeton.edu": "Princeton University",
    "caltech.edu": "California Institute of Technology",
    "columbia.edu": "Columbia University",
    "cornell.edu": "Cornell University",
    "yale.edu": "Yale University",
    "uchicago.edu": "University of Chicago",
    "umich.edu": "University of Michigan",
    "washington.edu": "University of Washington",
    # UK
    "cam.ac.uk": "University of Cambridge",
    "ox.ac.uk": "University of Oxford",
    "imperial.ac.uk": "Imperial College London",
    "ucl.ac.uk": "University College London",
    "ed.ac.uk": "University of Edinburgh",
    # Germany
    "mpg.de": "Max Planck Society",
    "tum.de": "Technical University of Munich",
    "kit.edu": "Karlsruhe Institute of Technology",
    # France
    "inria.fr": "Inria",
    "cnrs.fr": "CNRS",
    # Tech research labs — bios commonly link to corporate pages
    "google.com": "Google",
    "research.google": "Google Research",
    "deepmind.com": "DeepMind",
    "microsoft.com": "Microsoft Research",
    "research.facebook.com": "Meta AI Research",
    "ai.meta.com": "Meta AI Research",
    "nvidia.com": "NVIDIA",
    "openai.com": "OpenAI",
    "anthropic.com": "Anthropic",
    "ibm.com": "IBM Research",
}


# Patterns that pull candidate org strings out of free-text bios.
# Each captures a single group containing the candidate phrase.
_BIO_CANDIDATE_RES: tuple[re.Pattern[str], ...] = (
    # "at <Cap phrase>" — by far the most common bio shape
    re.compile(
        r"\bat\s+(@?[A-Z][\w&\-]*(?:\s+[A-Z][\w&\-]*){0,4})",
        re.UNICODE,
    ),
    # "@<handle>" — GitHub org / Twitter style references
    re.compile(r"(?<!\w)@([A-Za-z][\w\-]+)", re.UNICODE),
    # "<comma> <Cap phrase>" after a role
    re.compile(
        r",\s+(@?[A-Z][\w&\-]*(?:\s+[A-Z][\w&\-]*){0,4})\s*(?:[.,;]|$)",
        re.UNICODE,
    ),
    # "from <Cap phrase>" — "PhD from MIT"
    re.compile(
        r"\bfrom\s+(@?[A-Z][\w&\-]*(?:\s+[A-Z][\w&\-]*){0,4})",
        re.UNICODE,
    ),
)

# Single-token captures that aren't org names (the regex above is
# permissive — these are the most common false-positive captures).
_STOPWORD_CANDIDATES: frozenset[str] = frozenset({
    "i", "we", "a", "an", "the", "and", "or",
    "phd", "ms", "msc", "ba", "bsc", "dr",
    "research", "engineer", "scientist", "professor", "candidate",
    "student", "postdoc", "postdoctoral", "alumnus", "alumni",
    "founder", "co-founder", "ceo", "cto", "cso", "lead",
    "interested", "working", "building", "learning",
    "ml", "ai", "nlp", "cv", "se",
    "github", "twitter", "linkedin",
})


@dataclass(slots=True)
class BioAffiliationResult:
    """Summary of one stage run."""

    persons_examined: int
    persons_resolved: int
    candidates_extracted: int
    queries_attempted: int
    queries_accepted: int
    rejection_reasons: dict[str, int]


def _read_first(person: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty string value across the candidate keys."""
    for key in keys:
        value = person.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_bio_candidates(text: str) -> list[str]:
    """Pull candidate org strings out of free-text bio. Returns in
    order, deduped, with single-word stopwords filtered."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for pattern in _BIO_CANDIDATE_RES:
        for match in pattern.finditer(text):
            candidate = (match.group(1) or "").strip().rstrip(".,;:")
            if not candidate:
                continue
            lower = candidate.lower()
            if lower in _STOPWORD_CANDIDATES:
                continue
            # Drop solo `@phd`, `@ml`, etc. — handle-shaped stopwords.
            bare = lower.lstrip("@")
            if bare in _STOPWORD_CANDIDATES:
                continue
            if lower in seen:
                continue
            seen.add(lower)
            out.append(candidate)
    return out


def _blog_to_query(blog_url: str | None) -> str | None:
    """Parse a blog URL and return the ROR search query when the host
    matches a known institution suffix. Returns None on unknown hosts."""
    if not blog_url:
        return None
    s = blog_url.strip()
    if not s:
        return None
    if "://" not in s:
        s = "https://" + s
    try:
        host = (urlparse(s).hostname or "").lower()
    except Exception:  # noqa: BLE001 — best-effort parse
        return None
    if not host:
        return None
    parts = host.split(".")
    # Walk left → right: `lab.compbio.epfl.ch` tries `lab.compbio.epfl.ch`,
    # then `compbio.epfl.ch`, then `epfl.ch`. First DOMAIN_HINTS hit wins.
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in DOMAIN_HINTS:
            return DOMAIN_HINTS[candidate]
    return None


async def run_resolve_bio_to_ror_stage(
    *,
    reconciled: "ReconciledEntities",
    provider: RorRagProvider | None = None,
    skip_if_already_affiliated: bool = True,
) -> BioAffiliationResult:
    """Stage entry. Mutates each Person dict in ``reconciled.entities['persons']``
    that has an extractable affiliation signal in bio / orcid bio / blog.

    The function is idempotent: re-running on already-stamped persons
    leaves their `schema:affiliation` unchanged and (with
    ``skip_if_already_affiliated=True``, the default) won't even re-query.
    """
    provider = provider or build_default_provider()
    if provider is None:
        logger.warning(
            "resolve_bio_to_ror: RorRagProvider unavailable (check "
            "V2_ROR_RAG_ENABLED + INDEX_QDRANT_URL); stage skipped",
        )
        return BioAffiliationResult(0, 0, 0, 0, 0, {"provider_unavailable": 1})

    persons = reconciled.entities.get("persons") or []
    cache: dict[str, str | None] = {}
    reasons: dict[str, int] = {}
    candidates_extracted = 0
    resolved = 0

    for person in persons:
        if not isinstance(person, dict):
            continue
        if skip_if_already_affiliated and person.get(SCHEMA_AFFILIATION):
            continue

        bio = _read_first(person, BIO_KEYS) or ""
        orcid_bio = _read_first(person, ORCID_BIO_KEYS) or ""
        blog = _read_first(person, BLOG_KEYS) or ""

        candidates: list[str] = []
        seen_candidates: set[str] = set()
        for text in (bio, orcid_bio):
            for candidate in _extract_bio_candidates(text):
                if candidate.lower() in seen_candidates:
                    continue
                seen_candidates.add(candidate.lower())
                candidates.append(candidate)
        domain_query = _blog_to_query(blog)
        if domain_query and domain_query.lower() not in seen_candidates:
            candidates.append(domain_query)
            seen_candidates.add(domain_query.lower())
        if not candidates:
            continue
        candidates_extracted += len(candidates)

        candidate_rors: list[str] = []
        for candidate in candidates:
            ror_id, reason = await _resolve_one(provider, cache, candidate)
            if ror_id:
                if ror_id not in candidate_rors:
                    candidate_rors.append(ror_id)
            else:
                reasons[reason] = reasons.get(reason, 0) + 1

        if not candidate_rors:
            continue

        existing = person.get(SCHEMA_AFFILIATION)
        if isinstance(existing, str):
            merged = [existing] + [r for r in candidate_rors if r != existing]
        elif isinstance(existing, list):
            merged = list(existing) + [r for r in candidate_rors if r not in existing]
        else:
            merged = candidate_rors
        person[SCHEMA_AFFILIATION] = merged[0] if len(merged) == 1 else merged
        resolved += 1

    result = BioAffiliationResult(
        persons_examined=len(persons),
        persons_resolved=resolved,
        candidates_extracted=candidates_extracted,
        queries_attempted=len(cache),
        queries_accepted=sum(1 for v in cache.values() if v is not None),
        rejection_reasons=reasons,
    )
    logger.info(
        "resolve_bio_to_ror: persons_examined=%d persons_resolved=%d "
        "candidates=%d queries=%d accepted=%d",
        result.persons_examined,
        result.persons_resolved,
        result.candidates_extracted,
        result.queries_attempted,
        result.queries_accepted,
    )
    return result


__all__ = [
    "BIO_KEYS",
    "BLOG_KEYS",
    "BioAffiliationResult",
    "DOMAIN_HINTS",
    "ORCID_BIO_KEYS",
    "run_resolve_bio_to_ror_stage",
]
