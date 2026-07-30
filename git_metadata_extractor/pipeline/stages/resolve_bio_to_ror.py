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

from git_metadata_extractor.providers.ror_rag import RorRagProvider, build_default_provider
from git_metadata_extractor.pipeline.stages.resolve_company_to_ror import (
    _existing_membership_keys,
    _existing_org_ids,
    _materialise,
    _person_canonical_id,
    _resolve_one,
)

STAGE_SOURCE_TAG = "resolve_bio_to_ror"

if TYPE_CHECKING:
    from git_metadata_extractor.pipeline.stages.reconciliation import ReconciledEntities

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
EMAIL_KEYS = _multi_key("email")


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
    memberships_created: int
    organizations_created: int
    candidates_extracted: int
    queries_attempted: int
    queries_accepted: int
    rejection_reasons: dict[str, int]


def _persons_with_memberships(reconciled: "ReconciledEntities") -> set[str]:
    """Pre-compute the set of person ids that already have at least one
    Membership entry in the graph. Used to honour the
    `skip_if_already_affiliated` flag without re-checking on every
    person."""
    out: set[str] = set()
    for m in reconciled.entities.get("memberships") or []:
        if not isinstance(m, dict):
            continue
        composite: Any = None
        identifiers = m.get("identifiers")
        if isinstance(identifiers, dict):
            composite = identifiers.get("pulse:composite")
        if not isinstance(composite, str):
            composite = m.get("id")
        if isinstance(composite, str) and "__" in composite:
            out.add(composite.split("__", 1)[0])
    return out


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


def _email_to_query(email: str | None) -> str | None:
    """Pull the institution name out of a (privacy-anonymized) email.

    ``person_agent._anonymize_email`` already replaces the local part
    with a short SHA-256 prefix but keeps the domain intact — exactly
    the part we need for affiliation resolution. So
    ``deadbeef@epfl.ch`` resolves through ``DOMAIN_HINTS`` the same
    way ``people.epfl.ch`` does as a blog host.
    """
    if not isinstance(email, str):
        return None
    s = email.strip()
    if "@" not in s:
        return None
    domain = s.rsplit("@", 1)[1].strip().lower()
    if not domain:
        return None
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in DOMAIN_HINTS:
            return DOMAIN_HINTS[candidate]
    return None


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
    """Stage entry. For each Person whose bio / orcid bio / blog /
    email carries an institution signal, push a matching Membership
    (and Org stub, when new) onto ``reconciled.entities``.

    Idempotent: existing Memberships act as a de-dup set, so a
    re-run on a graph that already contains this stage's output is a
    no-op. With ``skip_if_already_affiliated=True`` (the default) the
    stage won't even query ROR for persons that already have any
    Membership — set to ``False`` to additively enrich.
    """
    provider = provider or build_default_provider()
    if provider is None:
        logger.warning(
            "resolve_bio_to_ror: RorRagProvider unavailable (check "
            "V2_ROR_RAG_ENABLED + INDEX_QDRANT_URL); stage skipped",
        )
        return BioAffiliationResult(0, 0, 0, 0, 0, 0, 0, {"provider_unavailable": 1})

    persons = reconciled.entities.get("persons") or []
    cache: dict[str, Any] = {}
    reasons: dict[str, int] = {}
    candidates_extracted = 0
    resolved = 0
    memberships_added = 0
    organizations_added = 0

    existing_org_ids = _existing_org_ids(reconciled)
    existing_membership_keys = _existing_membership_keys(reconciled)
    affiliated_persons = _persons_with_memberships(reconciled)

    for person in persons:
        if not isinstance(person, dict):
            continue
        person_id = _person_canonical_id(person)
        if not person_id:
            continue
        if skip_if_already_affiliated and person_id in affiliated_persons:
            continue

        bio = _read_first(person, BIO_KEYS) or ""
        orcid_bio = _read_first(person, ORCID_BIO_KEYS) or ""
        blog = _read_first(person, BLOG_KEYS) or ""
        email = _read_first(person, EMAIL_KEYS) or ""

        candidates: list[str] = []
        seen_candidates: set[str] = set()
        for text in (bio, orcid_bio):
            for candidate in _extract_bio_candidates(text):
                if candidate.lower() in seen_candidates:
                    continue
                seen_candidates.add(candidate.lower())
                candidates.append(candidate)
        # Email and blog feed the same DOMAIN_HINTS table; email comes
        # first because an institutional address (`@epfl.ch`) is a
        # stronger employer signal than a personal blog host.
        for query in (_email_to_query(email), _blog_to_query(blog)):
            if query and query.lower() not in seen_candidates:
                candidates.append(query)
                seen_candidates.add(query.lower())
        if not candidates:
            continue
        candidates_extracted += len(candidates)

        candidate_hits: list[dict[str, Any]] = []
        for candidate in candidates:
            hit, reason = await _resolve_one(provider, cache, candidate)
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
            resolved += 1
            memberships_added += m_added
            organizations_added += o_added
            affiliated_persons.add(person_id)

    result = BioAffiliationResult(
        persons_examined=len(persons),
        persons_resolved=resolved,
        memberships_created=memberships_added,
        organizations_created=organizations_added,
        candidates_extracted=candidates_extracted,
        queries_attempted=len(cache),
        queries_accepted=sum(1 for v in cache.values() if v is not None),
        rejection_reasons=reasons,
    )
    logger.info(
        "resolve_bio_to_ror: persons_examined=%d persons_resolved=%d "
        "memberships_created=%d organizations_created=%d "
        "candidates=%d queries=%d accepted=%d",
        result.persons_examined,
        result.persons_resolved,
        result.memberships_created,
        result.organizations_created,
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
    "EMAIL_KEYS",
    "ORCID_BIO_KEYS",
    "run_resolve_bio_to_ror_stage",
]
