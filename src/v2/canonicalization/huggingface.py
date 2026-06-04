"""Canonical HuggingFace URL builder for index ids.

HuggingFace's native ids are bare (`<namespace>/<name>` for repos, a bare
`<slug>` for users/orgs, an arXiv id for papers). v3.0.0 stores the canonical
URL as the id — consistent with the github/zenodo/openalex/ror index ids and
the extract pipeline's URL identifiers.

The per-surface URL prefix differs:
  model    -> https://huggingface.co/<repo_id>
  dataset  -> https://huggingface.co/datasets/<repo_id>
  space    -> https://huggingface.co/spaces/<repo_id>
  user/org -> https://huggingface.co/<slug>
  paper    -> https://huggingface.co/papers/<arxiv_id>
"""

from __future__ import annotations

_BASE = "https://huggingface.co/"
_PREFIX: dict[str, str] = {
    "model": "",
    "models": "",
    "dataset": "datasets/",
    "datasets": "datasets/",
    "space": "spaces/",
    "spaces": "spaces/",
    "user": "",
    "users": "",
    "org": "",
    "organization": "",
    "organizations": "",
    "paper": "papers/",
    "papers": "papers/",
}


def huggingface_iri(value: str | None, kind: str = "model") -> str | None:
    """Canonical HuggingFace URL for a bare id (or an already-canonical URL).

    Returns None on empty/invalid input. Idempotent: a value already under
    ``https://huggingface.co/`` is returned unchanged (trailing slash trimmed).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.lower().startswith(_BASE):
        return s.rstrip("/")
    if s.lower().startswith("http://huggingface.co/"):
        return ("https://" + s.split("://", 1)[1]).rstrip("/")
    prefix = _PREFIX.get(kind.lower().strip())
    if prefix is None:
        return None
    return f"{_BASE}{prefix}{s.strip('/')}"


__all__ = ["huggingface_iri"]
