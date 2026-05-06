"""Post-fetch scope filter for RenkuLab.

Renku has no first-class community concept (yet), so the `epfl` and
`switzerland` scopes match keyword substrings against namespace, path,
keywords, and repository URLs. Scope `all` keeps everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.index.renkulab.config import RenkulabIndexConfig


@dataclass(slots=True, frozen=True)
class Scope:
    name: str
    keywords: tuple[str, ...]


def resolve_scope(name: str, config: RenkulabIndexConfig) -> Scope:
    n = (name or "all").lower()
    if n == "all":
        return Scope(name="all", keywords=())
    if n == "epfl":
        return Scope(name="epfl", keywords=tuple(s.lower() for s in config.scope.epfl_keywords))
    if n == "switzerland":
        return Scope(
            name="switzerland",
            keywords=tuple(s.lower() for s in config.scope.switzerland_keywords),
        )
    message = f"Unknown scope: {name!r}. Expected one of: all, epfl, switzerland"
    raise ValueError(message)


def _haystack(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("namespace", "path", "slug"):
        v = item.get(key)
        if isinstance(v, str):
            parts.append(v)
    kw = item.get("keywords")
    if isinstance(kw, list):
        parts.extend(str(k) for k in kw)
    repos = item.get("repositories")
    if isinstance(repos, list):
        parts.extend(str(r) for r in repos)
    ns = item.get("namespace")
    if isinstance(ns, dict):
        for key in ("path", "slug"):
            v = ns.get(key)
            if isinstance(v, str):
                parts.append(v)
    return " ".join(parts).lower()


def matches(scope: Scope, item: dict[str, Any]) -> bool:
    if not scope.keywords:
        return True
    hay = _haystack(item)
    return any(kw in hay for kw in scope.keywords)
