# src/v2/canonicalization/gitlab.py
"""Canonical GitLab URL helpers for index ids.

GitLab's canonical landing-page URL is the id (v3.0.0), with the instance host
encoded in it so ids are globally unique across per-instance stores. The
GitLab API's ``web_url`` is authoritative; ``gitlab_iri`` is the builder used
when only a host + path is available.

  project -> https://<host>/<full_path>
  group   -> https://<host>/groups/<full_path>
  user    -> https://<host>/<username>
"""
from __future__ import annotations

# kind -> path prefix after the host (groups live under /groups/, others don't)
_PREFIX = {"project": "", "group": "groups/", "user": ""}


def gitlab_iri(host: str, kind: str, value: str | None) -> str | None:
    """Canonical GitLab URL for a bare path (or an already-canonical URL).

    Returns None on empty/invalid input. Raises ValueError on unknown ``kind``.
    Idempotent: a value already under ``https://<host>/`` is returned unchanged
    (trailing slash trimmed); an ``http://`` form is upgraded to ``https``.
    """
    if kind not in _PREFIX:
        msg = f"Unknown gitlab kind: {kind!r}"
        raise ValueError(msg)
    if not isinstance(value, str) or not value.strip():
        return None
    base = f"https://{host}/"
    s = value.strip()
    if s.lower().startswith(base):
        return s.rstrip("/")
    if s.lower().startswith(f"http://{host}/"):
        return ("https://" + s.split("://", 1)[1]).rstrip("/")
    if "://" in s:
        return None  # a URL on some other host
    return f"{base}{_PREFIX[kind]}{s.strip('/')}"


def parse_gitlab_iri(iri: str | None) -> tuple[str, str, str] | None:
    """Inverse: a canonical GitLab URL -> (host, kind, path). None otherwise."""
    if not isinstance(iri, str) or "://" not in iri:
        return None
    rest = iri.strip().rstrip("/").split("://", 1)[1]
    if "/" not in rest:
        return None
    host, path = rest.split("/", 1)
    if path.startswith("groups/"):
        return host, "group", path[len("groups/"):]
    kind = "user" if "/" not in path else "project"
    return host, kind, path


__all__ = ["gitlab_iri", "parse_gitlab_iri"]
