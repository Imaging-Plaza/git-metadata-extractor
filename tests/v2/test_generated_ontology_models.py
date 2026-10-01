"""Tests for the models generated from the SHACL shapes.

These assert the two things the hand-written JSON Schemas encoded by hand and
that the generator now derives: the identity hierarchy (`sh:or`) and the value
patterns. If either stops being enforced, the generator has regressed.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "git_metadata_extractor.schema.generated.canonical",
    reason="models not generated (just ontology-models-generate)",
)

from git_metadata_extractor.schema.generated import canonical

# --------------------------------------------------------------------------
# the identity disjunction becomes an enforced requirement
# --------------------------------------------------------------------------


def test_person_requires_an_identity() -> None:
    """`sh:or ( orcidIdentifier | hasProfile )` must fail at construction."""
    from pydantic import ValidationError  # noqa: PLC0415

    with pytest.raises(ValidationError, match="at least one of"):
        canonical.PersonModel(**{"schema:name": ["Nameless"]})


def test_person_accepts_either_identity_branch() -> None:
    by_orcid = canonical.PersonModel(
        **{"schema:name": ["A"], "pulse:orcidIdentifier": ["0000-0002-1825-0097"]},
    )
    by_profile = canonical.PersonModel(
        **{"schema:name": ["B"], "pulse:hasProfile": ["urn:pulse:profile-1"]},
    )

    assert by_orcid.pulse_orcidIdentifier == ["0000-0002-1825-0097"]
    assert by_profile.pulse_hasProfile == ["urn:pulse:profile-1"]


def test_organization_identity_is_ror_or_profile() -> None:
    from pydantic import ValidationError  # noqa: PLC0415

    with pytest.raises(ValidationError, match="at least one of"):
        canonical.OrganizationModel(**{"schema:name": ["Anon Org"]})

    # `pulse:ror` is a *list* here because the shape omits `sh:maxCount 1`,
    # even though an organization has one ROR. That is one of 14 properties
    # where the shapes are looser than the hand-written schemas they replace —
    # see ONTOLOGY_V3_REQUIREMENTS.md. Asserting the current behaviour so the
    # day someone tightens the shape, this test says so.
    ok = canonical.OrganizationModel(
        **{"schema:name": ["EPFL"], "pulse:ror": ["https://ror.org/02s376052"]},
    )
    assert ok.pulse_ror == ["https://ror.org/02s376052"]


# --------------------------------------------------------------------------
# patterns survive, including on multi-valued properties
# --------------------------------------------------------------------------


def test_pattern_is_enforced_inside_a_list() -> None:
    """The shapes omit `sh:maxCount 1` on several patterned properties.

    That makes them lists, and an earlier version of the generator dropped the
    pattern for exactly those — silently losing the ORCID and DOI formats. The
    pattern has to move inside the list annotation.
    """
    from pydantic import ValidationError  # noqa: PLC0415

    with pytest.raises(ValidationError, match="should match pattern"):
        canonical.PersonModel(
            **{"schema:name": ["A"], "pulse:orcidIdentifier": ["not-an-orcid"]},
        )


def test_orcid_pattern_is_the_bare_v3_form_not_a_url() -> None:
    """v3 uses bare identifiers; the URL form is a v2.1.2 habit.

    Documenting the migration rather than endorsing it — see §2.4 of
    ONTOLOGY_V3_REQUIREMENTS.md. Every ORCID in the live graph is a URL today,
    so this is the assertion that will fail loudly if the shapes flip back.
    """
    from pydantic import ValidationError  # noqa: PLC0415

    canonical.PersonModel(
        **{"schema:name": ["A"], "pulse:orcidIdentifier": ["0000-0002-1825-0097"]},
    )
    with pytest.raises(ValidationError, match="should match pattern"):
        canonical.PersonModel(
            **{
                "schema:name": ["A"],
                "pulse:orcidIdentifier": ["https://orcid.org/0000-0002-1825-0097"],
            },
        )


# --------------------------------------------------------------------------
# closed shapes reject unknown properties
# --------------------------------------------------------------------------


def test_closed_shapes_reject_unknown_properties() -> None:
    from pydantic import ValidationError  # noqa: PLC0415

    with pytest.raises(ValidationError, match=r"[Ee]xtra"):
        canonical.PersonModel(
            **{
                "schema:name": ["A"],
                "pulse:hasProfile": ["urn:pulse:p"],
                "pulse:notARealProperty": "x",
            },
        )


def test_platform_instance_reaches_the_generated_profile_models() -> None:
    """End-to-end proof that `ontology/patches/01` flows into the models."""
    profile = canonical.OrganizationProfileModel(
        **{
            "pulse:platform": "pulse:GitLab",
            "pulse:organizationHandle": "epfl",
            "pulse:platformInstance": "https://gitlab.epfl.ch",
        },
    )

    assert profile.pulse_platformInstance == "https://gitlab.epfl.ch"
