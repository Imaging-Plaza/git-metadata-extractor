"""Community projection keeps numeric/ISSN community slugs (field report #6 v2).

Earlier (#101) we dropped grant-number / ISSN-shaped community ids, assuming
they were junk. They are NOT: those are real Zenodo communities whose slug
happens to be the grant number (e.g. the BIORECER project community has slug
`101060684`) or a journal ISSN. Zenodo's direct `GET /api/communities/<slug>`
returns an empty envelope for numeric slugs, which made them look dead — but
that is a resolution problem solved by `fetch_by_slug`'s search fallback, not a
reason to discard the link. So `_project_communities` must keep every slug.
"""

from __future__ import annotations

from src.index.zenodo_records.ingest.records import _project_communities
from src.index.zenodo_records.iri import community_iri


def _item(*community_ids: str) -> dict:
    return {"metadata": {"communities": [{"id": c} for c in community_ids]}}


def test_grant_number_community_kept() -> None:
    # Record 19371895: ["101060684", "eu"] — BOTH kept; primary is the specific
    # project community (communities[0]).
    out = _project_communities(_item("101060684", "eu"))
    assert out == [community_iri("101060684"), community_iri("eu")]


def test_issn_community_kept() -> None:
    assert _project_communities(_item("1807-1260")) == [community_iri("1807-1260")]


def test_normal_slugs_kept() -> None:
    out = _project_communities(_item("epfl", "iccm-19"))
    assert out == [community_iri("epfl"), community_iri("iccm-19")]


def test_blank_and_missing_ids_skipped() -> None:
    assert _project_communities(_item("", "  ")) == []
    assert _project_communities({"metadata": {}}) == []
