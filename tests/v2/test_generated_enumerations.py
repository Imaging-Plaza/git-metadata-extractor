"""Tests for the enumerations generated from the ontology's instance triples.

Three things here are worth defending, because each was a real bug caught while
building the generator:

1. Enumerations are read as one **merged** graph. `PublicationType` is declared
   in the canonical file and extended by the raw file; per-layer reading emits
   two conflicting half-vocabularies.
2. Members are compacted to CURIEs. All 1606 disciplines are Wikidata entities,
   and the reader originally had no `wd:` prefix, so they emitted as full IRIs
   and matched none of the `wd:Q...` values the pipeline actually produces.
3. The shapes constrain enumerated properties with `sh:class`, never `sh:in`.
   The generator used to flatten those to `str`, so nothing rejected a
   `pulse:repositoryType` of `"pulse:Banana"`.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "git_metadata_extractor.schema.generated.enumerations",
    reason="models not generated (just ontology-models-generate)",
)

from pydantic import ValidationError

from git_metadata_extractor.schema.generated import canonical, enumerations

#: Mirrors `_LITERAL_MAX_MEMBERS` in scripts/v2/generate_from_ontology.py.
LITERAL_MAX_MEMBERS = 64

EXPECTED_ENUMERATIONS = {
    "AccessRight": 4,
    "Discipline": 1652,
    "IdentifierScheme": 11,
    "MembershipType": 3,
    "Modality": 6,
    "OrganizationType": 9,
    "Platform": 8,
    "PublicationType": 15,
    "RepositoryType": 7,
    "SpaceRuntimeStatus": 5,
    "SpaceSdk": 4,
    "Visibility": 3,
}


def _members(alias: str) -> frozenset[str]:
    import re  # noqa: PLC0415

    const = re.sub(r"(?<!^)(?=[A-Z])", "_", alias).upper()
    return getattr(enumerations, f"{const}_MEMBERS")


@pytest.mark.parametrize(("alias", "count"), sorted(EXPECTED_ENUMERATIONS.items()))
def test_enumeration_member_counts(alias: str, count: int) -> None:
    """Fails loudly if the submodule pin moves the vocabulary."""
    assert len(_members(alias)) == count


def test_all_twelve_enumerations_are_emitted() -> None:
    """Twelve, not six.

    The canonical file declares six enumeration classes and the raw file
    declares six more. Reading only the canonical layer — which an earlier plan
    assumed — silently drops Visibility, Modality, SpaceSdk,
    SpaceRuntimeStatus, MembershipType and IdentifierScheme.
    """
    for alias in EXPECTED_ENUMERATIONS:
        assert hasattr(enumerations, alias), f"{alias} not generated"
        assert _members(alias), f"{alias} has no members"


# --------------------------------------------------------------------------
# the cross-file merge
# --------------------------------------------------------------------------


def test_publication_type_merges_both_files() -> None:
    """7 canonical + 8 raw = 15, and both halves must be present."""
    members = enumerations.PUBLICATION_TYPE_MEMBERS

    assert "pulse:JournalArticle" in members, "canonical member missing"
    assert "pulse:SoftwarePublication" in members, "raw-file member missing"
    assert len(members) == EXPECTED_ENUMERATIONS["PublicationType"]


def test_canonical_shape_accepts_a_raw_file_enumeration_member() -> None:
    """The merge is not academic: it decides whether a real payload validates.

    `DepositShape` is canonical and constrains `pulse:publicationType` by
    `sh:class`. A promoted Zenodo software deposit carries
    `pulse:SoftwarePublication`, which is declared *only* in the raw file.
    """
    deposit = canonical.DepositModel(
        **{
            "pulse:platform": "pulse:Zenodo",
            "pulse:publicationType": ["pulse:SoftwarePublication"],
            "schema:datePublished": "2024-01-01",
        },
    )

    assert deposit.pulse_publicationType == ["pulse:SoftwarePublication"]


# --------------------------------------------------------------------------
# CURIE compaction
# --------------------------------------------------------------------------


def test_disciplines_are_curies_not_full_iris() -> None:
    """The pipeline emits `wd:Q...`; a full IRI here would match nothing."""
    members = enumerations.DISCIPLINE_MEMBERS

    assert not [m for m in members if m.startswith("http")]
    assert all(m.startswith("wd:Q") for m in members)


def test_no_enumeration_member_is_an_uncompacted_iri() -> None:
    for alias in EXPECTED_ENUMERATIONS:
        uncompacted = sorted(m for m in _members(alias) if m.startswith("http"))
        assert not uncompacted, f"{alias} has uncompacted members: {uncompacted[:3]}"


# --------------------------------------------------------------------------
# sh:class becomes an enforced Literal
# --------------------------------------------------------------------------


def test_enumerated_property_rejects_a_value_outside_the_vocabulary() -> None:
    with pytest.raises(ValidationError, match=r"literal_error|Input should be"):
        canonical.PlatformProfileModel(
            **{"pulse:platform": "pulse:Banana", "pulse:platformUsername": ["x"]},
        )


def test_enumerated_property_accepts_every_declared_member() -> None:
    for member in sorted(enumerations.PLATFORM_MEMBERS):
        # `pulse:platformUsername` satisfies the shape's own `sh:or` identity
        # requirement; without it the model rejects for an unrelated reason.
        profile = canonical.PlatformProfileModel(
            **{"pulse:platform": member, "pulse:platformUsername": ["someone"]},
        )
        assert profile.pulse_platform == member


# --------------------------------------------------------------------------
# the Literal/frozenset threshold
# --------------------------------------------------------------------------


def test_oversized_enumeration_degrades_to_str() -> None:
    """1606 members is past the threshold, so `Discipline` is not a `Literal`.

    A `Literal` that wide bloats the module and makes type checkers crawl, for
    a vocabulary that churns with each EPFL Graph refresh. The frozenset is the
    enforcement tool instead.
    """
    assert enumerations.Discipline is str
    assert len(enumerations.DISCIPLINE_MEMBERS) > LITERAL_MAX_MEMBERS


def test_small_enumerations_are_real_literals() -> None:
    from typing import get_args  # noqa: PLC0415

    assert set(get_args(enumerations.Platform)) == set(enumerations.PLATFORM_MEMBERS)
    assert set(get_args(enumerations.RepositoryType)) == set(
        enumerations.REPOSITORY_TYPE_MEMBERS,
    )


def test_labels_accompany_the_literal_sized_enumerations() -> None:
    """Labels replace the prose the hand-written schemas carried."""
    assert enumerations.PLATFORM_LABELS["pulse:HuggingFace"] == "Hugging Face"
    assert enumerations.ACCESS_RIGHT_LABELS["pulse:OpenAccess"] == "Open Access"
    # Skipped for the oversized one: 1606 label entries is noise.
    assert not hasattr(enumerations, "DISCIPLINE_LABELS")


# --------------------------------------------------------------------------
# the v3 discipline regression, pinned as a test so it cannot be forgotten
# --------------------------------------------------------------------------


def test_every_discipline_the_agents_may_emit_is_a_valid_v3_member() -> None:
    """Closes the gap documented in ONTOLOGY_V3_REQUIREMENTS.md §2.5.

    v3 as pinned declares only *leaf* disciplines as enumeration instances. The
    41 coarse categories our agent schema offers survived only as
    `rdfs:subClassOf` parents with no `rdf:type`, and the 5 faculty roots were
    absent entirely — so all 46 values the agents can emit failed
    `sh:class pulse:DisciplineEnumeration`. Zero of 46 were valid.

    `ontology/patches/03-coarse-discipline-tiers.patch` restores the two coarse
    tiers. This asserts the whole emittable vocabulary validates, so the patch
    silently falling out of the series is a test failure rather than a
    graph-wide violation discovered in production.
    """
    import re  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    agent_models = (
        Path(__file__).resolve().parents[2]
        / "git_metadata_extractor"
        / "schema"
        / "models"
        / "agent.py"
    )
    emittable = set(re.findall(r'"(wd:Q\d+)"', agent_models.read_text()))

    assert emittable, "no wd: disciplines found in the agent schema"
    invalid = sorted(emittable - enumerations.DISCIPLINE_MEMBERS)
    assert not invalid, (
        f"{len(invalid)} agent-emittable disciplines are not v3 enumeration "
        f"members: {invalid[:10]}. Is patch 03 applied? Run just ontology-prepare."
    )


def test_the_coarse_discipline_tiers_are_present_and_labelled() -> None:
    """Patch 03 restores 46 coarse terms on top of v3's 1606 leaves."""
    members = enumerations.DISCIPLINE_MEMBERS

    # A faculty root, absent from v3 entirely before the patch.
    assert "wd:Q7991" in members  # Natural sciences
    # A second-tier category, present before only as a subClassOf parent.
    assert "wd:Q428691" in members  # Computer engineering
    # The dangling "academic discipline" parent v2.1.2 referenced is NOT
    # reintroduced — it carries no signal as a tag.
    assert "wd:Q11862829" not in members
