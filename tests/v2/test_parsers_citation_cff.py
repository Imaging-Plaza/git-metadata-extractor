"""Tests for `src.v2.parsers.citation_cff.parse_citation_cff`.

Layer 1 contract: pure parser, no enrichment. We pin every section
of the v1.2.0 schema we surface, plus failure modes (malformed
YAML, wrong root type, dropped sub-trees), plus a real-world fixture
(sdsc-ordes/gimie's CITATION.cff) to catch spec drift.
"""

from __future__ import annotations

import textwrap

from src.v2.parsers.citation_cff import parse_citation_cff


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_returns_none_on_none_or_blank():
    assert parse_citation_cff(None) is None
    assert parse_citation_cff("") is None
    assert parse_citation_cff("   \n   ") is None


def test_returns_none_on_malformed_yaml():
    assert parse_citation_cff("foo: : bar") is None


def test_returns_none_when_top_level_is_not_a_mapping():
    """YAML list at the root is well-formed YAML but not a CITATION.cff."""
    assert parse_citation_cff("- a\n- b\n") is None


def test_returns_none_when_nothing_recognised():
    """Document loads but carries no recognised fields → None."""
    assert parse_citation_cff("foo: bar\n") is None


# ---------------------------------------------------------------------------
# Top-level scalar fields
# ---------------------------------------------------------------------------


def test_parses_minimal_required_fields():
    """`cff-version`, `title`, `message`, `version` — the most-common
    populated subset of the required-or-near-required fields."""
    doc = textwrap.dedent("""
        cff-version: 1.2.0
        title: My Software
        message: Please cite this software.
        version: 1.2.3
        date-released: 2024-05-01
        type: software
        url: https://example.org/software
        repository-code: https://github.com/example/software
        license: MIT
    """)
    out = parse_citation_cff(doc)
    assert out is not None
    assert out["cff-version"] == "1.2.0"
    assert out["title"] == "My Software"
    assert out["message"] == "Please cite this software."
    assert out["version"] == "1.2.3"
    assert out["date-released"] == "2024-05-01"
    assert out["type"] == "software"
    assert out["url"] == "https://example.org/software"
    assert out["repository-code"] == "https://github.com/example/software"
    assert out["license"] == "MIT"


def test_parses_keywords_as_list():
    out = parse_citation_cff(
        "cff-version: '1.2.0'\nkeywords:\n  - bioinformatics\n  - cryo-em\n",
    )
    assert out["keywords"] == ["bioinformatics", "cryo-em"]


def test_release_date_object_is_coerced_to_iso_string():
    """PyYAML decodes `YYYY-MM-DD` literals into `datetime.date` objects.
    The parser must coerce back to an ISO string so downstream
    consumers don't have to handle two types."""
    out = parse_citation_cff(
        "cff-version: '1.2.0'\ndate-released: 2024-05-01\n",
    )
    assert out["date-released"] == "2024-05-01"


# ---------------------------------------------------------------------------
# Authors / contacts (Person vs Entity discrimination)
# ---------------------------------------------------------------------------


def test_parses_person_authors_with_full_field_set():
    doc = textwrap.dedent("""
        cff-version: '1.2.0'
        authors:
          - given-names: Jane
            family-names: Doe
            orcid: https://orcid.org/0000-0001-2345-6789
            email: jane@example.org
            affiliation: EPFL
            website: https://jane.example
    """)
    out = parse_citation_cff(doc)
    a = out["authors"][0]
    assert a["__kind__"] == "person"
    assert a["given-names"] == "Jane"
    assert a["family-names"] == "Doe"
    assert a["orcid"] == "https://orcid.org/0000-0001-2345-6789"
    assert a["email"] == "jane@example.org"
    assert a["affiliation"] == "EPFL"
    assert a["website"] == "https://jane.example"


def test_parses_entity_authors_with_entity_kind_tag():
    """Authors can also be Entities (organizations / groups) — keyed by
    `name` instead of given/family-names. The `__kind__` tag is how
    downstream consumers tell the two apart without re-deriving."""
    doc = textwrap.dedent("""
        cff-version: '1.2.0'
        authors:
          - name: EPFL Center for Imaging
            website: https://imaging.epfl.ch
            country: CH
    """)
    out = parse_citation_cff(doc)
    e = out["authors"][0]
    assert e["__kind__"] == "entity"
    assert e["name"] == "EPFL Center for Imaging"
    assert e["website"] == "https://imaging.epfl.ch"
    assert e["country"] == "CH"


def test_drops_authors_with_neither_name_shape():
    """An author dict with no name fields at all is malformed per spec;
    drop it rather than emitting an empty record."""
    doc = textwrap.dedent("""
        cff-version: '1.2.0'
        authors:
          - given-names: Real Person
          - email: no-name@example.org
          - orcid: https://orcid.org/0000-0001-2345-6789
    """)
    out = parse_citation_cff(doc)
    # Only the first author has a usable name; the other two are dropped.
    assert len(out["authors"]) == 1
    assert out["authors"][0]["given-names"] == "Real Person"


def test_parses_contact_list_like_authors():
    out = parse_citation_cff(textwrap.dedent("""
        cff-version: '1.2.0'
        contact:
          - given-names: Bob
            family-names: Smith
            email: bob@example.org
    """))
    assert out["contact"][0]["email"] == "bob@example.org"
    assert out["contact"][0]["__kind__"] == "person"


# ---------------------------------------------------------------------------
# Identifiers (DOI / URL / SWH / other) + legacy `doi` promotion
# ---------------------------------------------------------------------------


def test_parses_identifiers_with_type_and_value():
    """DOI values are canonicalised to `https://doi.org/<bare>` via the
    shared `doi_iri` helper. URL and SWH identifiers pass through
    verbatim."""
    doc = textwrap.dedent("""
        cff-version: '1.2.0'
        identifiers:
          - type: doi
            value: 10.5281/zenodo.123456
            description: Software DOI
          - type: url
            value: https://example.org/canonical
          - type: swh
            value: swh:1:dir:abc123
    """)
    out = parse_citation_cff(doc)
    idents = out["identifiers"]
    assert {i["type"] for i in idents} == {"doi", "url", "swh"}
    doi = next(i for i in idents if i["type"] == "doi")
    assert doi["value"] == "https://doi.org/10.5281/zenodo.123456"
    assert doi["description"] == "Software DOI"
    # URL identifiers pass through unchanged.
    url = next(i for i in idents if i["type"] == "url")
    assert url["value"] == "https://example.org/canonical"


def test_doi_canonicalisation_handles_every_input_shape():
    """`doi_iri` accepts bare DOIs, `doi:`-prefixed, legacy
    `dx.doi.org` host, and already-canonical URLs. The parser must
    use it uniformly across every DOI source — `identifiers[type=doi]`,
    legacy top-level `doi`, `preferred-citation.doi`, and `references[].doi`."""
    out = parse_citation_cff(textwrap.dedent("""
        cff-version: '1.2.0'
        identifiers:
          - type: doi
            value: 10.5281/zenodo.AAA
          - type: doi
            value: doi:10.5281/zenodo.BBB
          - type: doi
            value: https://dx.doi.org/10.5281/zenodo.CCC
          - type: doi
            value: https://doi.org/10.5281/zenodo.DDD
        preferred-citation:
          type: article
          title: Paper
          doi: 10.1234/paper.bare
        references:
          - type: article
            title: Ref
            doi: doi:10.9999/ref.prefixed
    """))
    doi_values = [
        i["value"] for i in out["identifiers"] if i["type"] == "doi"
    ]
    assert doi_values == [
        "https://doi.org/10.5281/zenodo.AAA",
        "https://doi.org/10.5281/zenodo.BBB",
        "https://doi.org/10.5281/zenodo.CCC",
        "https://doi.org/10.5281/zenodo.DDD",
    ]
    assert out["preferred-citation"]["doi"] == "https://doi.org/10.1234/paper.bare"
    assert out["references"][0]["doi"] == "https://doi.org/10.9999/ref.prefixed"


def test_legacy_doi_field_promotes_into_identifiers():
    """v1.0/1.1 used a top-level `doi` field; v1.2 moved it into
    `identifiers`. Real-world CITATION.cff files still ship both
    shapes — we promote the legacy form so downstream code can iterate
    `identifiers` uniformly. The promoted entry is also canonicalised
    to the URL form."""
    out = parse_citation_cff(textwrap.dedent("""
        cff-version: '1.2.0'
        doi: 10.5281/zenodo.999
    """))
    # Top-level `doi` canonicalised to URL form (was bare in YAML).
    assert out["doi"] == "https://doi.org/10.5281/zenodo.999"
    # AND promoted into identifiers as a synthetic doi entry, also URL form.
    promoted = next(i for i in out["identifiers"] if i["type"] == "doi")
    assert promoted["value"] == "https://doi.org/10.5281/zenodo.999"


def test_legacy_doi_not_duplicated_when_already_in_identifiers():
    """If `identifiers` already contains the same DOI (in any form),
    the canonical comparison post-promotion still de-dups."""
    out = parse_citation_cff(textwrap.dedent("""
        cff-version: '1.2.0'
        doi: 10.5281/zenodo.999
        identifiers:
          - type: doi
            value: 10.5281/zenodo.999
    """))
    dois = [i for i in out["identifiers"] if i["type"] == "doi"]
    assert len(dois) == 1
    assert dois[0]["value"] == "https://doi.org/10.5281/zenodo.999"


# ---------------------------------------------------------------------------
# preferred-citation + references
# ---------------------------------------------------------------------------


def test_parses_preferred_citation_with_authors_and_doi():
    doc = textwrap.dedent("""
        cff-version: '1.2.0'
        preferred-citation:
          type: article
          title: A Paper About The Software
          doi: 10.1234/paper.example
          year: 2024
          journal: Journal of Examples
          authors:
            - given-names: Jane
              family-names: Doe
              orcid: https://orcid.org/0000-0001-2345-6789
    """)
    out = parse_citation_cff(doc)
    pc = out["preferred-citation"]
    assert pc["type"] == "article"
    assert pc["title"] == "A Paper About The Software"
    assert pc["doi"] == "https://doi.org/10.1234/paper.example"
    assert pc["year"] == "2024"
    assert pc["journal"] == "Journal of Examples"
    assert pc["authors"][0]["given-names"] == "Jane"


def test_parses_references_list():
    doc = textwrap.dedent("""
        cff-version: '1.2.0'
        references:
          - type: article
            title: First reference
            doi: 10.1234/first
          - type: conference-paper
            title: Second reference
            year: 2023
    """)
    out = parse_citation_cff(doc)
    refs = out["references"]
    assert len(refs) == 2
    assert refs[0]["doi"] == "https://doi.org/10.1234/first"
    assert refs[1]["year"] == "2023"


# ---------------------------------------------------------------------------
# Robustness against malformed sub-trees
# ---------------------------------------------------------------------------


def test_skips_malformed_subtree_without_failing_whole_parse():
    """A wrong-shape `authors` field (string instead of list) drops
    just that field — the rest of the doc still surfaces."""
    out = parse_citation_cff(textwrap.dedent("""
        cff-version: '1.2.0'
        title: foo
        authors: "this should be a list"
        license: MIT
    """))
    assert out["title"] == "foo"
    assert out["license"] == "MIT"
    assert "authors" not in out


# ---------------------------------------------------------------------------
# Real-world fixture: sdsc-ordes/gimie
# ---------------------------------------------------------------------------


def test_parses_real_world_gimie_citation_cff():
    """Pinned snapshot of `sdsc-ordes/gimie/CITATION.cff` to catch
    spec drift. Verifies all six authors, all five keywords, and the
    full set of populated top-level fields."""
    from pathlib import Path

    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "citation_cff_gimie.cff"
    )
    out = parse_citation_cff(fixture.read_text(encoding="utf-8"))
    assert out is not None

    assert out["cff-version"] == "1.2.0"
    assert out["title"] == "gimie"
    assert out["type"] == "software"
    assert out["repository-code"] == "https://github.com/sdsc-ordes/gimie"
    assert out["abstract"] == "Extract linked metadata from repositories"
    assert out["license"] == "Apache-2.0"
    assert "message" in out  # multi-line `>-` block scalar

    # 6 authors, all Persons (Cyril, Sabine, Robin, Martin, Laure, Stefan).
    assert len(out["authors"]) == 6
    cyril = next(
        a for a in out["authors"] if a.get("given-names") == "Cyril"
    )
    assert cyril["family-names"] == "Matthey-Doret"
    assert cyril["affiliation"] == "Swiss Data Science Center"
    assert cyril["orcid"] == "https://orcid.org/0000-0002-1126-1535"
    assert cyril["__kind__"] == "person"

    # One author has email (Stefan) — verify the field surfaces.
    stefan = next(
        a for a in out["authors"] if a.get("given-names") == "Stefan"
    )
    assert stefan["email"] == "supermegaiperste@hotmail.com"

    # Keywords list intact.
    assert out["keywords"] == [
        "git", "cli", "library", "linked-open-data",
        "metadata-extraction", "fair-data", "scientific-software",
    ]
