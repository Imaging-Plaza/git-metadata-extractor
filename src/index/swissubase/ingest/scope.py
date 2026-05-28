"""Scope filter builder for the SWISSUbase indexer.

The catalogue itself has no institution filter, so the scope is applied
as a post-filter on the rendered institution string of each study. The
result drives the ``affiliation_match`` boolean on ``studies`` rows,
which the embedder uses to decide what to vectorise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Literal

if TYPE_CHECKING:
    from src.index.swissubase.config import SwissubaseIndexConfig

ScopeName = Literal["epfl_sdsc_ethz", "switzerland"]


@dataclass(slots=True, frozen=True)
class Scope:
    name: str
    patterns: tuple[str, ...]

    def matches(self, *texts: str | None) -> bool:
        """True if any of `patterns` is found (case-insensitively) in any text.

        ``switzerland`` resolves to an empty pattern tuple — every study
        matches, so embedding covers the full catalogue.
        """
        if not self.patterns:
            return True
        haystacks = [t.lower() for t in texts if t]
        if not haystacks:
            return False
        for pattern in self.patterns:
            needle = pattern.lower()
            if any(needle in h for h in haystacks):
                return True
        return False


def epfl_sdsc_ethz_scope(config: SwissubaseIndexConfig) -> Scope:
    return Scope(
        name="epfl_sdsc_ethz",
        patterns=tuple(config.scope.epfl_sdsc_ethz_patterns),
    )


def switzerland_scope(_: SwissubaseIndexConfig) -> Scope:
    # Empty pattern tuple → every study matches. Use this when you want
    # to embed the whole Swiss social-science catalogue.
    return Scope(name="switzerland", patterns=())


def resolve_scope(name: str, config: SwissubaseIndexConfig) -> Scope:
    if name == "epfl_sdsc_ethz":
        return epfl_sdsc_ethz_scope(config)
    if name == "switzerland":
        return switzerland_scope(config)
    message = (
        f"Unknown scope: {name}. Known: epfl_sdsc_ethz, switzerland"
    )
    raise ValueError(message)


def institution_strings(institutions: Iterable[dict]) -> list[str]:
    """Flatten an institution-block list to plain strings for matching."""
    out: list[str] = []
    for inst in institutions:
        if not isinstance(inst, dict):
            continue
        for key in ("name", "title", "label", "displayName", "address"):
            v = inst.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v)
    return out
