"""Tests for SPARQL term serialisation — the one injection surface.

SPARQL over HTTP has no prepared statements, so every query this service sends
is assembled as text. Two consumers build them: the provenance writer (values
extracted from arbitrary repositories) and the graph query API (an IRI straight
off an HTTP request). Both go through `store/terms.py`.

The asymmetry between literals and IRIs is the design, not an inconsistency:

- **Literals are escaped**, by rdflib. A name holding a quote, a brace or a
  literal `<< nested star >>` round-trips unchanged.
- **IRIs are refused**, because `URIRef.n3()` does *not* escape. An IRI with a
  character RDF forbids in an IRIREF would close the term early and whatever
  followed would parse as SPARQL. For a caller's input that is a 400; for a
  value the pipeline produced it is a dropped annotation, recoverable by
  re-running. A corrupted statement is not.

This file also carries the prefix-subset test that `unify/runner.py`'s comment
claimed existed and did not — the same cited-but-missing test
`raw_projection.py` had. Two instances of the same mistake in one refactor is
enough to distrust any docstring that names a test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdflib import Graph

from git_metadata_extractor.store.terms import (
    COMPACT_PREFIXES,
    UnusableIRIError,
    compact,
    datetime_term,
    expand,
    iri_term,
    literal_term,
    term,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "v2"))

STARS = 1497


# --------------------------------------------------------------------------
# literals: escaped, and round-tripping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "plain",
        'a "quoted" name',
        "back\\slash",
        "line\nbreak",
        "tab\there",
        "emoji \N{PARTY POPPER} and <angle> brackets",
        "semi; colon . dot",
        "}} braces {{",
        # The injection case that matters most: a value that looks like the
        # syntax it is being embedded in.
        "<< nested star >>",
        "'single quotes'",
        '"""triple quoted"""',
    ],
)
def test_a_literal_round_trips_through_a_parser(value: str) -> None:
    """Asserted as a round trip, not against expected text.

    The text is not the contract, and guessing it is how an earlier version of
    this test failed on three of four cases: rdflib wraps a newline-bearing
    value in Turtle long-quotes rather than escaping it, and leaves a tab raw.
    Both are valid and neither is what a hand-written expectation predicts.
    """
    parsed = Graph().parse(
        data=f"<urn:s> <urn:p> {literal_term(value)} .",
        format="turtle",
    )

    assert [str(obj) for _s, _p, obj in parsed] == [value]


def test_an_integer_keeps_its_datatype() -> None:
    """A count serialised as a plain string would not compare or sort."""
    text = literal_term(STARS)

    assert "1497" in text
    assert "XMLSchema#integer" in text


def test_a_boolean_is_not_typed_as_an_integer() -> None:
    """`isinstance(True, int)` is true in Python.

    So the obvious ordering of the type checks types every boolean as an
    integer, and `pulse:archived true` becomes `1`.
    """
    assert "boolean" in literal_term(value=True)
    assert "integer" in literal_term(1)


def test_a_datetime_is_typed() -> None:
    assert "XMLSchema#dateTime" in datetime_term("2026-09-09T11:00:00")


# --------------------------------------------------------------------------
# IRIs: refused, not escaped
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://example.org/a>b",
        "https://example.org/a<b",
        'https://example.org/a"b',
        "https://example.org/a b",
        "https://example.org/a\nb",
        "https://example.org/a\\b",
        "https://example.org/a{b}",
        "https://example.org/a|b",
        "https://example.org/a^b",
        "https://example.org/a`b",
        # The one that would actually do damage.
        "https://x/a> } ; DROP GRAPH <urn:pulse:graph:canonical> #",
    ],
)
def test_an_unusable_iri_is_refused(value: str) -> None:
    with pytest.raises(UnusableIRIError):
        iri_term(value)


def test_an_empty_iri_is_refused() -> None:
    """`<>` is a legal relative IRI and resolves against the base — so an
    empty string would silently become whatever the store's base URI is."""
    with pytest.raises(UnusableIRIError):
        iri_term("")


@pytest.mark.parametrize(
    "value",
    [
        "https://orcid.org/0000-0002-1825-0097",
        "urn:pulse:profile:github:jane",
        "https://github.com/some_org/repo-name.v2",
        "https://ror.org/02s376052",
        "urn:pulse:output:1160e8e3-8632-42e0-beb4-40dd539224cc:github",
        # A composite id, which contains a full IRI and two underscores.
        "https://orcid.org/0000-0002-1825-0097__https://github.com/a/b",
    ],
)
def test_a_real_iri_is_accepted(value: str) -> None:
    """Every IRI shape this pipeline actually mints has to pass.

    A validator that rejects `urn:pulse:...` or a composite id would refuse
    most of the graph.
    """
    assert iri_term(value) == f"<{value}>"


def test_term_dispatches_on_the_reference_flag() -> None:
    """`<< s p "https://…" >>` and `<< s p <https://…> >>` are different.

    So an annotation with the wrong term form quotes a triple that is not in
    the canonical graph, and nothing errors — a quoted triple need not exist to
    be annotated.
    """
    value = "https://spdx.org/licenses/MIT.html"

    assert term(value, is_reference=True) == f"<{value}>"
    assert term(value, is_reference=False).startswith('"')


# --------------------------------------------------------------------------
# the prefix table
# --------------------------------------------------------------------------


def test_compact_and_expand_are_inverse() -> None:
    for base, prefix in COMPACT_PREFIXES:
        full = f"{base}thing"
        assert compact(full) == f"{prefix}thing"
        assert expand(f"{prefix}thing") == full


def test_an_unknown_namespace_passes_through_unchanged() -> None:
    """Better a full IRI than a wrong CURIE.

    `https://spdx.org/licenses/MIT.html` has no prefix here, and inventing one
    would produce a term nothing can expand.
    """
    iri = "https://spdx.org/licenses/MIT.html"

    assert compact(iri) == iri
    assert expand(iri) == iri


@pytest.mark.skipif(
    not (REPO_ROOT / "vendor" / "open-pulse-ontology" / "src" / "ontology").is_dir(),
    reason="ontology submodule not checked out",
)
def test_the_compact_prefixes_are_a_subset_of_the_readers() -> None:
    """The test `unify/runner.py` claimed existed, and did not.

    `ontology_reader.PREFIXES` is the authority — it drives both the CURIE
    compactor and the JSON-LD context emitter — but it lives under `scripts/`
    and cannot be imported by production code. So this table is a copy, and a
    second copy of a prefix table is exactly how a context ends up unable to
    expand the terms its own models emit (§3c of the handoff: the reader had no
    `wd:` prefix and all 1,606 disciplines silently matched nothing).

    A *subset* rather than equal: the reader carries prefixes only the
    generator needs (`skos:`, `dct:`, `coar-*`), and production code has no
    use for them.
    """
    from ontology_reader import PREFIXES

    # `PREFIXES` is a tuple of `(prefix, namespace)` pairs, not a mapping.
    reader = {str(base): str(prefix) for prefix, base in PREFIXES}
    if not reader:
        pytest.skip("reader PREFIXES is empty; nothing to compare")

    mismatched = [
        (base, prefix)
        for base, prefix in COMPACT_PREFIXES
        if base in reader and reader[base] != prefix.rstrip(":")
    ]
    assert not mismatched, f"prefix disagreement with the reader: {mismatched}"

    unknown = [base for base, _prefix in COMPACT_PREFIXES if base not in reader]
    assert not unknown, f"namespaces the reader does not declare: {unknown}"
