"""Scope resolver — turns a scope name into a list of seed org slugs.

Two scopes are supported in v1: `epfl` and `switzerland`. The seed lists
live in `config/index/huggingface.yaml` under `scope.seeds`. New orgs
discovered via `discover-orgs` are surfaced for human review and only
promoted into the YAML by hand — never auto-added.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.index.huggingface.config import HuggingFaceIndexConfig

ScopeName = Literal["epfl", "switzerland"]


@dataclass(slots=True, frozen=True)
class Scope:
    """A resolved scope: name + the curated seed org slugs to ingest."""

    name: str
    seeds: list[str]


def resolve_scope(name: ScopeName, config: HuggingFaceIndexConfig) -> Scope:
    return Scope(name=name, seeds=config.seed_for(name))
