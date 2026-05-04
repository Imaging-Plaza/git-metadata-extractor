"""Discover + Hydrate protocols for the federated index layer.

Sibling to `IndexAdapter` in `registry.py`, which covers
`search` / `lookup`. These protocols cover the inverse — building up
each index's local DuckDB by:

1. **Discover** — produce candidate identifiers (`Seed`s) from some
   source (web scrape, citation graph, search query, dump diff,
   sibling-index extract).
2. **Hydrate** — for each `Seed`, fetch the canonical record and
   upsert into the index's local store.

See `.internal/federated/discover-hydrate-design.md` for the full
design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Seed:
    """A candidate identifier produced by `IndexDiscoverer.discover()`.

    Cross-index by construction: the `seed_type` string routes the seed
    to the hydrator(s) that accept it. Hints carry opaque per-source
    metadata (e.g. an affiliation ROR to stamp at hydration time).
    """

    id: str
    """Canonical id form. DOI URL, ORCID id, OpenAlex id (full URL or short),
    GitHub repo URL, ROR id, etc. The hydrator is responsible for
    normalising as needed."""

    seed_type: str
    """Discriminator. Examples: ``"doi"``, ``"orcid"``, ``"openalex_work"``,
    ``"openalex_author"``, ``"zenodo_id"``, ``"github_repo"``, ``"ror"``.
    Open string by design — adding a new seed type does not require a
    protocol change. Each hydrator declares which strings it accepts via
    ``accepted_seed_types``."""

    source: str
    """Where the seed was found. Examples: ``"datascience.ch"``,
    ``"openalex_search"``, ``"infoscience_citations"``, ``"from-references"``,
    ``"by-ror"``. Useful for provenance and dedup across sources."""

    hint: dict[str, Any] | None = None
    """Optional extra metadata for the hydrator. Free-form: e.g.
    ``{"affiliation_ror": "https://ror.org/02hdt9m26"}`` to stamp an
    institution link, or ``{"refs_only": True}`` to skip work upsert and
    only populate references."""

    def to_jsonl_dict(self) -> dict[str, Any]:
        d = {"id": self.id, "seed_type": self.seed_type, "source": self.source}
        if self.hint:
            d["hint"] = self.hint
        return d

    @classmethod
    def from_jsonl_dict(cls, data: dict[str, Any]) -> Seed:
        return cls(
            id=data["id"],
            seed_type=data["seed_type"],
            source=data["source"],
            hint=data.get("hint"),
        )


@dataclass
class HydrationSummary:
    """Aggregate counts returned from a single ``hydrate()`` call.

    All counters are inclusive — ``fetched`` includes both ``in_scope``
    and ``out_of_scope`` (when the hydrator does post-filtering, like
    ORCID does for Switzerland scope). ``skipped_existing`` counts seeds
    short-circuited by ``only_unfetched``.
    """

    fetched: int = 0
    in_scope: int = 0
    out_of_scope: int = 0
    skipped_existing: int = 0
    errors: int = 0
    extras: dict[str, Any] = field(default_factory=dict)
    """Hydrator-specific counters that don't fit the standard buckets
    (e.g. ``stamped_affiliations`` for OpenAlex when ``hint.affiliation_ror``
    is set)."""

    def merge(self, other: HydrationSummary) -> HydrationSummary:
        merged = HydrationSummary(
            fetched=self.fetched + other.fetched,
            in_scope=self.in_scope + other.in_scope,
            out_of_scope=self.out_of_scope + other.out_of_scope,
            skipped_existing=self.skipped_existing + other.skipped_existing,
            errors=self.errors + other.errors,
        )
        merged.extras = {**self.extras, **other.extras}
        return merged


@runtime_checkable
class IndexDiscoverer(Protocol):
    """Produce :class:`Seed`s from some named source.

    Discoverers are *not* required to be 1:1 with an index — a
    discoverer registered under one index name can emit seeds whose
    ``seed_type`` is consumed by a *different* index's hydrator.
    Example: ``openalex.from_datascience_ch`` emits ``seed_type="doi"``
    seeds; both OpenAlex's hydrator (fetches the work) and (in future)
    Zenodo's hydrator (resolves Zenodo DOIs to records) can consume
    them.

    Implementations live under ``src/index/<name>/`` and self-register
    in their ``_federated.py`` (or equivalent) at import time.
    """

    name: str
    """Short discoverer name. Often the index module name (``"openalex"``)
    but may include a discriminator (``"openalex.references"``)."""

    accepted_sources: tuple[str, ...]
    """Whitelist of ``--source`` strings this discoverer recognises."""

    def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
        """Yield seeds for ``source``.

        Implementations must validate ``source in self.accepted_sources``
        and raise ``ValueError`` otherwise. Source-specific options are
        passed via ``**opts`` (e.g. ``query="..."`` for search-based
        discovery, ``url="..."`` for scrape-based).
        """
        ...


@runtime_checkable
class IndexHydrator(Protocol):
    """Persist canonical records for a stream of :class:`Seed`s.

    Hydrators are 1:1 with an index — each hydrator owns the writes to
    one index's local store. ``hydrate()`` must be idempotent: passing
    the same seed twice (or many times) must not produce duplicate
    rows.
    """

    name: str
    """Short hydrator name — by convention the index module name."""

    accepted_seed_types: tuple[str, ...]
    """Whitelist of ``seed_type`` strings this hydrator can resolve."""

    def hydrate(
        self,
        seeds: Iterable[Seed],
        *,
        only_unfetched: bool = True,
    ) -> HydrationSummary:
        """Fetch and upsert each seed.

        With ``only_unfetched=True`` (default), seeds whose ``id`` is
        already present in the local store are short-circuited and
        counted in ``HydrationSummary.skipped_existing``.

        Seeds whose ``seed_type`` is not in
        :attr:`accepted_seed_types` are silently ignored — the
        federated dispatcher is responsible for routing.
        """
        ...
