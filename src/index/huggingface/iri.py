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


__all__ = [
    "dataset_iri",
    "iri_for_entity_type",
    "model_iri",
    "namespace_iri",
    "parse_namespace_slug",
    "parse_repo_id",
    "space_iri",
]
