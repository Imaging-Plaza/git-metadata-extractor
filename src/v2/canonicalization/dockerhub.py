"""Canonical Docker Hub URL builder for index ids.

Official images (the ``library`` namespace) live at ``/_/<name>``; everything
else at ``/r/<namespace>/<name>``.
"""

from __future__ import annotations

_BASE = "https://hub.docker.com/"


def dockerhub_iri(namespace: str | None, name: str | None) -> str | None:
    """Canonical Docker Hub repo URL from ``(namespace, name)``.

    ``library/python`` -> https://hub.docker.com/_/python
    ``grafana/grafana`` -> https://hub.docker.com/r/grafana/grafana
    Returns None when ``name`` is missing.
    """
    if not isinstance(name, str) or not name.strip():
        return None
    n = name.strip().strip("/")
    ns = (namespace or "library").strip().strip("/") or "library"
    if not n:
        return None
    if ns == "library":
        return f"{_BASE}_/{n}"
    return f"{_BASE}r/{ns}/{n}"


__all__ = ["dockerhub_iri"]
