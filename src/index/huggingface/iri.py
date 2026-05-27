"""Canonical IRI helpers for the HuggingFace index.

The bare keys we used as PKs are namespace-shaped strings — `epfl-llm`
for an org, `epfl-llm/meditron-7b` for a model. Every other catalog in
the graph already uses real dereferenceable URLs as its `@id`, so this
module promotes HF too. URL shapes (mirroring HF's own routing):

  org / user (same URL pattern)  https://huggingface.co/<slug>
  model                          https://huggingface.co/<author>/<name>
  dataset                        https://huggingface.co/datasets/<author>/<name>
  space                          https://huggingface.co/spaces/<author>/<name>

No trailing slash. Helpers are idempotent and tolerate legacy `www.`
host and trailing slashes on input.
"""

from __future__ import annotations

import re as _re

_BASE = "https://huggingface.co/"
_WWW = "https://www.huggingface.co/"
_DATASETS_PREFIX = _BASE + "datasets/"
_SPACES_PREFIX = _BASE + "spaces/"


def _strip_base(s: str) -> str:
    if s.startswith(_WWW):
        return s[len(_WWW) :]
    if s.startswith(_BASE):
        return s[len(_BASE) :]
    return s


def _normalise(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().rstrip("/")
    return s or None


def namespace_iri(slug: str) -> str:
    """`epfl-llm` → `https://huggingface.co/epfl-llm`. Covers users + orgs."""
    s = _normalise(slug)
    if not s:
        return s or ""
    if s.startswith(_BASE) or s.startswith(_WWW):
        return _BASE + _strip_base(s).rstrip("/")
    return _BASE + s


def model_iri(repo_id: str) -> str:
    """`epfl-llm/meditron-7b` → `https://huggingface.co/epfl-llm/meditron-7b`."""
    s = _normalise(repo_id)
    if not s:
        return s or ""
    bare = _strip_base(s).rstrip("/")
    # If a caller mis-typed `datasets/...` / `spaces/...` we still
    # produce a sane URL — but the caller probably wanted dataset_iri /
    # space_iri instead, so just return the literal join.
    return _BASE + bare


def dataset_iri(repo_id: str) -> str:
    """`epfl-llm/guidelines` → `https://huggingface.co/datasets/epfl-llm/guidelines`."""
    s = _normalise(repo_id)
    if not s:
        return s or ""
    bare = _strip_base(s).rstrip("/")
    if bare.startswith("datasets/"):
        return _DATASETS_PREFIX + bare[len("datasets/") :]
    return _DATASETS_PREFIX + bare


def space_iri(repo_id: str) -> str:
    """`EPFL-VILAB/4M` → `https://huggingface.co/spaces/EPFL-VILAB/4M`."""
    s = _normalise(repo_id)
    if not s:
        return s or ""
    bare = _strip_base(s).rstrip("/")
    if bare.startswith("spaces/"):
        return _SPACES_PREFIX + bare[len("spaces/") :]
    return _SPACES_PREFIX + bare


def iri_for_entity_type(entity_type: str, bare: str) -> str:
    """Dispatch on a `chunks.entity_type` discriminator value.

    ``entity_type`` is one of ``model | dataset | space | org``.
    """
    match entity_type:
        case "model":
            return model_iri(bare)
        case "dataset":
            return dataset_iri(bare)
        case "space":
            return space_iri(bare)
        case "org":
            return namespace_iri(bare)
    # Unknown discriminator: return as-is; safer than fabricating an URL.
    return bare


# --- Inverses -------------------------------------------------------------


def parse_namespace_slug(iri_or_bare: str) -> str | None:
    """`https://huggingface.co/epfl-llm` → `epfl-llm`. Bare input passes through."""
    s = _normalise(iri_or_bare)
    if not s:
        return None
    s = _strip_base(s)
    # Trim repo path if caller mis-passed a full repo IRI to the namespace parser.
    if s.startswith("datasets/"):
        s = s[len("datasets/") :]
    elif s.startswith("spaces/"):
        s = s[len("spaces/") :]
    # First path segment is the namespace.
    return s.split("/", 1)[0] or None


def parse_repo_id(iri_or_bare: str) -> str | None:
    """Strip any `huggingface.co[/datasets|/spaces]` prefix from a repo IRI.

    Returns the bare `<author>/<name>` form regardless of input shape.
    """
    s = _normalise(iri_or_bare)
    if not s:
        return None
    s = _strip_base(s)
    for prefix in ("datasets/", "spaces/"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return s or None


# ---------------------------------------------------------------------------
# Citation-surface helpers (arXiv DOIs from tags, BibTeX DOI extraction,
# paperswithcode URLs). HF doesn't expose a `card_data.doi` for any of our
# 1,515 repos, so these derived fields are the closest we get to a real
# citation column.
# ---------------------------------------------------------------------------

_DOI_URL_PREFIX = "https://doi.org/"
_ARXIV_DOI_PREFIX = "10.48550/arXiv."
_PAPERSWITHCODE_BASE = "https://paperswithcode.com/dataset/"
_ARXIV_TAG_RE = _re.compile(r"^arxiv:(.+?)(?:v\d+)?$", _re.IGNORECASE)
# Conservative DOI shape — `10.<reg>/<suffix>` where suffix is non-greedy
# up to whitespace, quote, bracket, or `}` (BibTeX delimiter).
_BIBTEX_DOI_RE = _re.compile(
    r"\b(10\.\d{4,9}/[^\s\"'<>{}]+)",
    _re.IGNORECASE,
)


def arxiv_doi_iri(arxiv_id: str) -> str | None:
    """`2311.16079` → `https://doi.org/10.48550/arXiv.2311.16079`.

    Accepts bare arxiv ids, the `arxiv:<id>` tag form, or already-built
    DOI URLs. Returns None for empty / unparseable input. Strips an
    optional version suffix (`v1`, `v2`, …) since DOIs target the
    abstract record, not a specific version.
    """
    s = _normalise(arxiv_id)
    if not s:
        return None
    if s.startswith(_DOI_URL_PREFIX):
        return s.rstrip("/")
    match = _ARXIV_TAG_RE.match(s)
    if match:
        s = match.group(1)
    # Strip version suffix when caller passed just `<id>v2`.
    if not s.startswith(_ARXIV_DOI_PREFIX):
        s_no_ver = _re.sub(r"v\d+$", "", s, flags=_re.IGNORECASE)
        s = _ARXIV_DOI_PREFIX + s_no_ver
    return _DOI_URL_PREFIX + s


def arxiv_dois_from_tags(tags: list[str] | None) -> list[str]:
    """Extract DOI URLs from a model's `tags` list. Order preserved, deduped."""
    if not tags:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if not isinstance(tag, str) or not tag.lower().startswith("arxiv:"):
            continue
        iri = arxiv_doi_iri(tag)
        if iri and iri not in seen:
            seen.add(iri)
            out.append(iri)
    return out


def paperswithcode_url(paperswithcode_id: str | None) -> str | None:
    """`mnist` → `https://paperswithcode.com/dataset/mnist`. Idempotent on URLs."""
    s = _normalise(paperswithcode_id)
    if not s:
        return None
    if s.startswith(_PAPERSWITHCODE_BASE):
        return s.rstrip("/")
    if s.startswith("http://") or s.startswith("https://"):
        return s.rstrip("/")
    return _PAPERSWITHCODE_BASE + s


def dois_from_bibtex(citation_text: str | None) -> list[str]:
    """Pull every `10.<reg>/<suffix>` substring out of a BibTeX string.

    Returns canonical `https://doi.org/...` URLs, order preserved, deduped.
    Empty / non-string input returns `[]` (datasets without a citation).
    """
    if not isinstance(citation_text, str) or not citation_text.strip():
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _BIBTEX_DOI_RE.finditer(citation_text):
        bare = match.group(1).rstrip(".,;:")  # strip trailing punctuation
        iri = _DOI_URL_PREFIX + bare
        if iri not in seen:
            seen.add(iri)
            out.append(iri)
    return out


__all__ = [
    "arxiv_doi_iri",
    "arxiv_dois_from_tags",
    "dataset_iri",
    "dois_from_bibtex",
    "iri_for_entity_type",
    "model_iri",
    "namespace_iri",
    "paperswithcode_url",
    "parse_namespace_slug",
    "parse_repo_id",
    "space_iri",
]
