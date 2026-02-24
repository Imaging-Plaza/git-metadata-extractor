from __future__ import annotations

from dataclasses import dataclass

from src.v2.agents import ProviderSet
from src.v2.canonicalization.organization_alias_map import OrganizationAliasResolver
from src.v2.graph.store import GraphStore
from src.v2.providers.base import RORProvider
from src.v2.providers.mock_github import MockGitHubProvider
from src.v2.providers.mock_ror import MockRORProvider

CANONICAL_ROR_ID = "https://ror.org/02s376052"
HIGH_CONFIDENCE_THRESHOLD = 0.9
LOW_CONFIDENCE_THRESHOLD = 0.5
NO_LOOKUP_ASSERTION_MESSAGE = "search_organizations should not be called"


@dataclass
class _NoLookupRORProvider(RORProvider):
    calls: int = 0

    def get_organization(self, _ror_id: str) -> dict[str, object]:
        self.calls += 1
        return {}

    def search_organizations(self, _query: str) -> list[dict[str, object]]:
        self.calls += 1
        raise AssertionError(NO_LOOKUP_ASSERTION_MESSAGE)


class _LowConfidenceRORProvider(RORProvider):
    def get_organization(self, _ror_id: str) -> dict[str, object]:
        return {}

    def search_organizations(self, _query: str) -> list[dict[str, object]]:
        return [
            {
                "id": CANONICAL_ROR_ID,
                "name": "Completely Different Institute",
                "aliases": ["CDI"],
            },
        ]


def _providers(*, ror: RORProvider | None = None) -> ProviderSet:
    return ProviderSet(
        github=MockGitHubProvider(),
        ror=ror,
    )


def _build_store(tmp_path) -> GraphStore:
    return GraphStore(str(tmp_path / "org_alias_canonicalization.db"))


def test_epfl_resolves_to_same_canonical_id_as_swiss_name(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="organization",
        entity_id=CANONICAL_ROR_ID,
        data={"schema:name": "Swiss Federal Institute of Technology Lausanne"},
        identifiers={"ror": CANONICAL_ROR_ID},
        id_source="ror",
    )
    resolver = OrganizationAliasResolver(store)

    swiss = resolver.resolve(
        "Swiss Federal Institute of Technology Lausanne",
        _providers(ror=MockRORProvider()),
    )
    epfl = resolver.resolve("EPFL", _providers(ror=MockRORProvider()))

    assert swiss.canonical_id == CANONICAL_ROR_ID
    assert epfl.canonical_id == CANONICAL_ROR_ID


def test_accented_name_matches_epfl_canonical_id(tmp_path) -> None:
    store = _build_store(tmp_path)
    resolver = OrganizationAliasResolver(store)

    epfl = resolver.resolve("EPFL", _providers(ror=MockRORProvider()))
    accented = resolver.resolve(
        "Ecole Polytechnique Fédérale de Lausanne",
        _providers(ror=MockRORProvider()),
    )

    assert epfl.canonical_id == accented.canonical_id
    assert accented.confidence >= HIGH_CONFIDENCE_THRESHOLD


def test_exact_alias_hit_resolves_without_ror_lookup(tmp_path) -> None:
    store = _build_store(tmp_path)
    store.insert_entity(
        entity_type="organization",
        entity_id="org-epfl",
        data={"schema:name": "EPFL"},
        identifiers={"ror": CANONICAL_ROR_ID},
        id_source="ror",
    )
    store.insert_alias("EPFL", "org-epfl", 0.99, "manual")
    resolver = OrganizationAliasResolver(store)
    ror_provider = _NoLookupRORProvider()

    resolution = resolver.resolve("EPFL", _providers(ror=ror_provider))

    assert resolution.canonical_id == "org-epfl"
    assert resolution.source == "manual"
    assert ror_provider.calls == 0


def test_high_confidence_ror_match_maps_and_stores_alias(tmp_path) -> None:
    store = _build_store(tmp_path)
    resolver = OrganizationAliasResolver(store)

    resolution = resolver.resolve("EPFL", _providers(ror=MockRORProvider()))
    alias = store.lookup_alias("EPFL")
    entity = store.get_entity(CANONICAL_ROR_ID)

    assert resolution.canonical_id == CANONICAL_ROR_ID
    assert resolution.confidence > HIGH_CONFIDENCE_THRESHOLD
    assert resolution.source == "ror"
    assert resolution.is_new_entity is False
    assert alias is not None
    assert alias.source == "ror"
    assert alias.canonical_entity_id == CANONICAL_ROR_ID
    assert entity is not None


def test_low_confidence_match_creates_distinct_org_entity(tmp_path) -> None:
    store = _build_store(tmp_path)
    resolver = OrganizationAliasResolver(store)

    resolution = resolver.resolve(
        "Independent Data Lab",
        _providers(ror=_LowConfidenceRORProvider()),
    )
    entity = store.get_entity(resolution.canonical_id)

    assert resolution.is_new_entity is True
    assert resolution.confidence < LOW_CONFIDENCE_THRESHOLD
    assert resolution.canonical_id != CANONICAL_ROR_ID
    assert entity is not None
    assert entity.id_source == "uuid"


def test_normalization_examples() -> None:
    assert OrganizationAliasResolver.normalize_string("  EPFL  ") == "epfl"
    assert OrganizationAliasResolver.normalize_string("É.P.F.L.") == "epfl"


def test_alias_provenance_source_is_recorded(tmp_path) -> None:
    store = _build_store(tmp_path)
    resolver = OrganizationAliasResolver(store)

    resolver.resolve("EPFL", _providers(ror=MockRORProvider()))
    resolver.resolve(
        "Independent Data Lab",
        _providers(ror=_LowConfidenceRORProvider()),
    )
    epfl_alias = store.lookup_alias("EPFL")
    low_conf_alias = store.lookup_alias("Independent Data Lab")

    assert epfl_alias is not None
    assert low_conf_alias is not None
    assert epfl_alias.source in {"ror", "agent", "manual", "derived"}
    assert low_conf_alias.source in {"ror", "agent", "manual", "derived"}
