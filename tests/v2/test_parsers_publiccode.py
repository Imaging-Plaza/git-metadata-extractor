"""Tests for `git_metadata_extractor.parsers.publiccode.parse_publiccode`.

We cover every section the v0.4 core schema declares plus a handful
of failure modes (malformed YAML, wrong top-level type, missing
optional sub-trees). Country extensions (``it:`` etc.) are also
passed through.
"""

from __future__ import annotations

import textwrap

from git_metadata_extractor.parsers.publiccode import parse_publiccode


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_returns_none_on_none_or_blank():
    assert parse_publiccode(None) is None
    assert parse_publiccode("") is None
    assert parse_publiccode("   \n   ") is None


def test_returns_none_on_malformed_yaml():
    assert parse_publiccode("foo: : bar") is None


def test_returns_none_when_top_level_is_not_a_mapping():
    """A YAML list at the root is well-formed YAML but not a
    publiccode document."""
    assert parse_publiccode("- a\n- b\n") is None


def test_returns_none_when_nothing_recognised():
    """Document loads but carries no recognised v0.4 fields → None
    (caller treats this as 'effectively empty')."""
    assert parse_publiccode("foo: bar\n") is None


# ---------------------------------------------------------------------------
# Top-level scalar + list fields
# ---------------------------------------------------------------------------


def test_parses_minimal_required_fields():
    """The fields most likely to be filled in by real public-sector
    repos: yml version, name, repo URL, license. Coerced to strings
    even when YAML loads them as numbers (releaseDate as a date is
    YAML-native but we want str)."""
    doc = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        name: My Service
        url: https://github.com/agency/my-service
        softwareVersion: 1.2.3
        releaseDate: 2024-05-01
        developmentStatus: stable
        softwareType: standalone/web
    """)
    out = parse_publiccode(doc)
    assert out is not None
    assert out["publiccodeYmlVersion"] == "0.4.0"
    assert out["name"] == "My Service"
    assert out["url"] == "https://github.com/agency/my-service"
    assert out["softwareVersion"] == "1.2.3"
    assert out["releaseDate"] == "2024-05-01"
    assert out["developmentStatus"] == "stable"
    assert out["softwareType"] == "standalone/web"


def test_parses_list_fields():
    doc = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        platforms:
          - web
          - linux
        categories:
          - office
          - document-management
        usedBy:
          - City of Bologna
          - City of Florence
    """)
    out = parse_publiccode(doc)
    assert out["platforms"] == ["web", "linux"]
    assert out["categories"] == ["office", "document-management"]
    assert out["usedBy"] == ["City of Bologna", "City of Florence"]


def test_is_based_on_coerces_singular_string_to_list():
    """Spec says ``isBasedOn`` is string OR list-of-strings; we
    normalise to list so callers don't have to branch."""
    out = parse_publiccode(
        "publiccodeYmlVersion: '0.4.0'\nisBasedOn: https://example.com/upstream\n",
    )
    assert out["isBasedOn"] == ["https://example.com/upstream"]


# ---------------------------------------------------------------------------
# Structured sub-trees
# ---------------------------------------------------------------------------


def test_parses_intended_audience():
    doc = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        intendedAudience:
          countries:
            - it
            - fr
          unsupportedCountries:
            - cn
          scope:
            - government
            - education
    """)
    out = parse_publiccode(doc)
    assert out["intendedAudience"] == {
        "countries": ["it", "fr"],
        "unsupportedCountries": ["cn"],
        "scope": ["government", "education"],
    }


def test_parses_legal():
    doc = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        legal:
          license: AGPL-3.0-or-later
          mainCopyrightOwner: City of Bologna
          repoOwner: City of Bologna
          authorsFile: AUTHORS.md
    """)
    out = parse_publiccode(doc)
    assert out["legal"] == {
        "license": "AGPL-3.0-or-later",
        "mainCopyrightOwner": "City of Bologna",
        "repoOwner": "City of Bologna",
        "authorsFile": "AUTHORS.md",
    }


def test_parses_maintenance_with_contractors_and_contacts():
    doc = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        maintenance:
          type: contract
          contractors:
            - name: ACME Corp
              until: 2025-12-31
              website: https://acme.example
          contacts:
            - name: Jane Smith
              email: jane@example.org
              affiliation: City of Bologna
              phone: '+39 051 1234567'
    """)
    out = parse_publiccode(doc)
    maintenance = out["maintenance"]
    assert maintenance["type"] == "contract"
    assert maintenance["contractors"] == [
        {"name": "ACME Corp", "until": "2025-12-31", "website": "https://acme.example"},
    ]
    assert maintenance["contacts"] == [
        {
            "name": "Jane Smith",
            "email": "jane@example.org",
            "affiliation": "City of Bologna",
            "phone": "+39 051 1234567",
        },
    ]


def test_parses_localisation_with_boolean_and_languages():
    doc = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        localisation:
          localisationReady: true
          availableLanguages:
            - en
            - it
            - fr
    """)
    out = parse_publiccode(doc)
    assert out["localisation"] == {
        "localisationReady": True,
        "availableLanguages": ["en", "it", "fr"],
    }


def test_parses_depends_on_buckets():
    doc = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        dependsOn:
          open:
            - name: PostgreSQL
              versionMin: '13'
              optional: false
          proprietary:
            - name: Oracle Database
              version: '19c'
              optional: true
          hardware:
            - name: NVIDIA T4
              optional: true
    """)
    out = parse_publiccode(doc)
    assert out["dependsOn"]["open"] == [
        {"name": "PostgreSQL", "versionMin": "13", "optional": False},
    ]
    assert out["dependsOn"]["proprietary"] == [
        {"name": "Oracle Database", "version": "19c", "optional": True},
    ]
    assert out["dependsOn"]["hardware"] == [
        {"name": "NVIDIA T4", "optional": True},
    ]


def test_parses_description_with_multiple_languages():
    """Description is per-language; we keep the map intact so
    consumers can pick (English-preferring callers can do
    ``out['description'].get('en') or next(iter(out['description'].values()))``)."""
    doc = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        description:
          en:
            localisedName: My Service
            genericName: Records Manager
            shortDescription: A document management system.
            longDescription: |
              Long description here.
              Multiple paragraphs.
            documentation: https://docs.example/en
            apiDocumentation: https://api.example/en
            features:
              - search
              - audit-trail
            screenshots:
              - https://example.com/s1.png
            awards:
              - Best of OpenGov 2023
          it:
            shortDescription: Un sistema di gestione documentale.
            longDescription: Versione italiana.
    """)
    out = parse_publiccode(doc)
    description = out["description"]
    assert set(description.keys()) == {"en", "it"}
    en = description["en"]
    assert en["localisedName"] == "My Service"
    assert en["genericName"] == "Records Manager"
    assert en["shortDescription"] == "A document management system."
    assert en["longDescription"].startswith("Long description")
    assert en["documentation"] == "https://docs.example/en"
    assert en["features"] == ["search", "audit-trail"]
    assert en["awards"] == ["Best of OpenGov 2023"]
    assert description["it"]["shortDescription"].startswith("Un sistema")


# ---------------------------------------------------------------------------
# Forward-compat: country extensions and unknown top-level keys
# ---------------------------------------------------------------------------


def test_passes_country_extension_block_through_verbatim():
    """The `it:` country extension declares Italy-specific fields
    (riuso, codiceIPA). We don't model those individually but the
    payload is dict-shaped, so we pass it through so callers can
    inspect what they need."""
    doc = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        name: foo
        it:
          countryExtensionVersion: '1.0'
          riuso:
            codiceIPA: city_of_bologna
          piattaforme:
            spid: true
    """)
    out = parse_publiccode(doc)
    assert out["it"]["countryExtensionVersion"] == "1.0"
    assert out["it"]["riuso"] == {"codiceIPA": "city_of_bologna"}
    assert out["it"]["piattaforme"] == {"spid": True}


def test_does_not_pass_non_dict_unknown_keys_through():
    """An unknown top-level key whose value isn't a dict (e.g. someone
    declared a random scalar) is ignored — only structured forward-
    extension blocks are forwarded."""
    out = parse_publiccode(
        "publiccodeYmlVersion: '0.4.0'\nname: foo\nrandomScalar: hello\n",
    )
    assert "randomScalar" not in out


# ---------------------------------------------------------------------------
# Robustness against malformed sub-trees
# ---------------------------------------------------------------------------


def test_parses_real_world_foodsoft_publiccode():
    """Regression-pin against a real publiccode.yml in the wild
    (https://github.com/foodcoops/foodsoft @ 1e3adaef). The doc
    declares `publiccodeYmlVersion: 0.2` (older than v0.4), exercising
    forward compatibility — every field present in v0.2 is also in
    v0.4 and parses cleanly."""
    from pathlib import Path

    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "publiccode_foodsoft_1e3adaef.yml"
    )
    out = parse_publiccode(fixture.read_text(encoding="utf-8"))
    assert out is not None
    # Top-level scalars.
    assert out["publiccodeYmlVersion"] == "0.2"
    assert out["name"] == "Foodsoft"
    assert out["url"] == "https://github.com/foodcoops/foodsoft.git"
    assert out["landingURL"] == "https://foodcoops.net"
    assert out["releaseDate"] == "2025-03-20"
    assert out["softwareVersion"] == "v4.9.1"
    assert out["developmentStatus"] == "stable"
    assert out["softwareType"] == "standalone/web"
    # Lists.
    assert out["platforms"] == ["web"]
    assert out["categories"] == [
        "e-commerce",
        "business-process-management",
        "project-collaboration",
        "warehouse-management",
    ]
    # Structured sub-trees.
    assert out["maintenance"] == {"type": "community"}
    assert out["legal"] == {"license": "AGPL-3.0-or-later"}
    assert out["localisation"] == {
        "localisationReady": True,
        "availableLanguages": ["de", "en", "es", "fr", "nl", "tr"],
    }
    en = out["description"]["en"]
    assert en["genericName"] == "Webshop"
    assert "food coops" in en["shortDescription"]
    assert "cooperatives" in en["longDescription"]
    assert "Order management system" in en["features"]
    assert len(en["features"]) == 9


def test_skips_malformed_subtrees_without_failing_whole_parse():
    """A wrong-shape `legal` block shouldn't make the whole parse return
    None — we still get the other fields."""
    doc = textwrap.dedent("""
        publiccodeYmlVersion: '0.4.0'
        name: foo
        legal: "this should be a dict but is a string"
        platforms:
          - web
    """)
    out = parse_publiccode(doc)
    assert out["name"] == "foo"
    assert out["platforms"] == ["web"]
    assert "legal" not in out  # silently dropped
