"""Non-community identifiers must not pollute `community_ids` /
`primary_community_id` (field report #6).

Some Zenodo records carry EU grant numbers (`101060684`) and journal ISSNs
(`1807-1260`) under `metadata.communities`; blindly wrapping them produces
`…/communities/<id>` URLs that 404. `_project_communities` must drop them.
"""

from __future__ import annotations

from src.index.zenodo_records.ingest.records import (
    _is_plausible_community_slug,
    _project_communities,
)
from src.index.zenodo_records.iri import community_iri


def _item(*community_ids: str) -> dict:
    return {"metadata": {"communities": [{"id": c} for c in community_ids]}}


def test_grant_number_dropped_real_community_kept() -> None:
    # Record 19371895 from the report: ["101060684", "eu"] → only "eu" survives.
    out = _project_communities(_item("101060684", "eu"))
    assert out == [community_iri("eu")]


def test_issn_only_yields_no_community() -> None:
    # Record 4008755 from the report: ["1807-1260"] → empty (no false community).
    assert _project_communities(_item("1807-1260")) == []


def test_valid_slugs_all_kept() -> None:
    out = _project_communities(_item("epfl", "iccm-19", "hubble_tension_resolution"))
    assert out == [
        community_iri("epfl"),
        community_iri("iccm-19"),
        community_iri("hubble_tension_resolution"),
    ]


def test_slug_validator_patterns() -> None:
    # grants (pure numeric, 4+) and ISSNs are rejected
    for bad in ("101060684", "44994575", "220725", "10061983", "1807-1260", "1234"):
        assert not _is_plausible_community_slug(bad), bad
    # real community slugs are accepted
    for good in ("eu", "epfl", "iccm-19", "cern", "spi-ace", "aams2025microcity"):
        assert _is_plausible_community_slug(good), good
