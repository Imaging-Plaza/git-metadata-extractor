"""Smoke tests for the discover/hydrate protocols, registry, and dispatcher.

No network, no DuckDB. Mock discoverers/hydrators register with the
real registry under unique names and exercise the dispatcher.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

import pytest

from src.index._federated import dh_registry
from src.index._federated.dh_registry import (
    dispatch_hydrate,
    register_discoverer,
    register_hydrator,
)
from src.index._federated.protocols import (
    HydrationSummary,
    Seed,
)


@pytest.fixture(autouse=True)
def _isolate_registries(monkeypatch):
    """Each test runs against a fresh dict so we don't leak state."""
    monkeypatch.setattr(dh_registry, "DISCOVERERS", {})
    monkeypatch.setattr(dh_registry, "HYDRATORS", {})
    yield


def test_seed_roundtrips_jsonl():
    seed = Seed(
        id="https://openalex.org/W123",
        seed_type="openalex_work",
        source="from-search",
        hint={"query": "ml", "k": 5},
    )
    line = json.dumps(seed.to_jsonl_dict(), ensure_ascii=False)
    parsed = Seed.from_jsonl_dict(json.loads(line))
    assert parsed == seed


def test_seed_without_hint_omits_field():
    seed = Seed(id="x", seed_type="t", source="s")
    d = seed.to_jsonl_dict()
    assert "hint" not in d


def test_register_rejects_non_protocol_object():
    class BadDiscoverer:
        name = "bad"
        # Missing accepted_sources + discover()

    with pytest.raises(TypeError):
        register_discoverer(BadDiscoverer())


def test_dispatch_routes_by_seed_type():
    captured: dict[str, list[Seed]] = {"a": [], "b": []}

    class HydratorA:
        name = "a"
        accepted_seed_types = ("type_a",)

        def hydrate(self, seeds: Iterable[Seed], *, only_unfetched: bool = True) -> HydrationSummary:
            captured["a"].extend(list(seeds))
            return HydrationSummary(fetched=len(captured["a"]))

    class HydratorB:
        name = "b"
        accepted_seed_types = ("type_b",)

        def hydrate(self, seeds: Iterable[Seed], *, only_unfetched: bool = True) -> HydrationSummary:
            captured["b"].extend(list(seeds))
            return HydrationSummary(fetched=len(captured["b"]))

    register_hydrator(HydratorA())
    register_hydrator(HydratorB())

    seeds = [
        Seed(id="1", seed_type="type_a", source="t"),
        Seed(id="2", seed_type="type_a", source="t"),
        Seed(id="3", seed_type="type_b", source="t"),
        Seed(id="4", seed_type="type_unknown", source="t"),
    ]
    summaries = dispatch_hydrate(seeds, only_unfetched=True)

    # A got both type_a seeds; B got only type_b; unknown silently ignored.
    assert [s.id for s in captured["a"]] == ["1", "2"]
    assert [s.id for s in captured["b"]] == ["3"]
    assert summaries["a"].fetched == 2
    assert summaries["b"].fetched == 1


def test_dispatch_fans_out_to_multiple_hydrators_for_same_type():
    """If two hydrators accept the same seed_type, both receive the seeds."""
    counts: dict[str, int] = {"left": 0, "right": 0}

    class Left:
        name = "left"
        accepted_seed_types = ("doi",)

        def hydrate(self, seeds, *, only_unfetched=True):
            counts["left"] = len(list(seeds))
            return HydrationSummary(fetched=counts["left"])

    class Right:
        name = "right"
        accepted_seed_types = ("doi",)

        def hydrate(self, seeds, *, only_unfetched=True):
            counts["right"] = len(list(seeds))
            return HydrationSummary(fetched=counts["right"])

    register_hydrator(Left())
    register_hydrator(Right())

    seeds = [Seed(id="10.1/x", seed_type="doi", source="t")]
    dispatch_hydrate(seeds)
    assert counts == {"left": 1, "right": 1}


def test_dispatcher_catches_hydrator_exceptions():
    class Boom:
        name = "boom"
        accepted_seed_types = ("doi",)

        def hydrate(self, seeds, *, only_unfetched=True):
            raise RuntimeError("kaboom")

    register_hydrator(Boom())
    seeds = [
        Seed(id="a", seed_type="doi", source="t"),
        Seed(id="b", seed_type="doi", source="t"),
    ]
    summaries = dispatch_hydrate(seeds)
    # Errors counted, no exception propagated.
    assert summaries["boom"].errors == 2


def test_only_unfetched_flag_passes_through():
    seen_flag: dict[str, bool] = {}

    class Recorder:
        name = "recorder"
        accepted_seed_types = ("x",)

        def hydrate(self, seeds, *, only_unfetched=True):
            seen_flag["only_unfetched"] = only_unfetched
            list(seeds)
            return HydrationSummary()

    register_hydrator(Recorder())
    seeds = [Seed(id="1", seed_type="x", source="s")]
    dispatch_hydrate(seeds, only_unfetched=False)
    assert seen_flag == {"only_unfetched": False}


def test_only_filter_restricts_to_named_hydrators():
    class A:
        name = "a"
        accepted_seed_types = ("t",)

        def hydrate(self, seeds, *, only_unfetched=True):
            return HydrationSummary(fetched=len(list(seeds)))

    class B:
        name = "b"
        accepted_seed_types = ("t",)

        def hydrate(self, seeds, *, only_unfetched=True):
            return HydrationSummary(fetched=len(list(seeds)))

    register_hydrator(A())
    register_hydrator(B())
    seeds = [Seed(id="1", seed_type="t", source="s")]
    # Even though both accept "t", only_filter restricts to "a".
    summaries = dispatch_hydrate(seeds, only=["a"])
    assert set(summaries) == {"a"}


def test_discoverer_yields_seeds():
    class FakeDiscoverer:
        name = "fake"
        accepted_sources = ("source-x",)

        def discover(self, source: str, **opts: Any) -> Iterator[Seed]:
            assert source == "source-x"
            for i in range(3):
                yield Seed(id=f"id-{i}", seed_type="t", source=source)

    register_discoverer(FakeDiscoverer())
    disc = dh_registry.DISCOVERERS["fake"]
    out = list(disc.discover("source-x"))
    assert [s.id for s in out] == ["id-0", "id-1", "id-2"]


def test_hydration_summary_merge():
    a = HydrationSummary(fetched=1, in_scope=1, errors=0, extras={"k": 1})
    b = HydrationSummary(fetched=2, out_of_scope=1, errors=1, extras={"k": 9, "j": 7})
    merged = a.merge(b)
    assert merged.fetched == 3
    assert merged.in_scope == 1
    assert merged.out_of_scope == 1
    assert merged.errors == 1
    assert merged.extras == {"k": 9, "j": 7}  # b overrides a
