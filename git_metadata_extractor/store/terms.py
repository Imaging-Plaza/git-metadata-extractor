"""Serialise Python values as SPARQL terms, safely.

SPARQL over HTTP has no prepared statements, so every query this service sends
is assembled as text. That makes term serialisation the one place an injection
can happen — and the values involved come out of arbitrary repositories
(`schema:name`), or straight off an HTTP request (an IRI a caller asked to look
up). So it lives here, once, tested, rather than inline at each call site.

Two rules, and the asymmetry between them is deliberate:

- **Literals are escaped**, by rdflib's `Literal.n3()`. A name holding a quote,
  a backslash, a brace or a literal `<< nested star >>` round-trips unchanged;
  hand-rolling that is how a name becomes an injection. Verified against a live
  store with eight adversarial values.
- **IRIs are refused**, because `URIRef.n3()` does *not* escape. An IRI
  containing a character RDF forbids in an IRIREF would produce a broken or —
  worse — a silently altered statement, so it raises instead. For a caller's
  input that becomes a 400; for a value the pipeline produced it becomes a
  dropped annotation, which is recoverable by re-running. A corrupted statement
  is not.
"""

from __future__ import annotations

from typing import Any

from rdflib import Literal, URIRef
from rdflib.namespace import XSD

#: Characters RDF 1.1 forbids inside an `IRIREF`, plus whitespace. `<` and `>`
#: would terminate the term; `"` and `\` would escape it; the rest are simply
#: illegal and a store may or may not say so.
FORBIDDEN_IRI_CHARACTERS = '<>"{}|^`\\'


class UnusableIRIError(ValueError):
    """An IRI that cannot be written as a SPARQL term."""


def iri_term(value: str) -> str:
    """An IRI as a SPARQL term, or raise `UnusableIRIError`."""
    if not value:
        message = "IRI is empty"
        raise UnusableIRIError(message)
    if any(char in value for char in FORBIDDEN_IRI_CHARACTERS) or any(
        char.isspace() for char in value
    ):
        message = f"IRI is not usable as a SPARQL term: {value!r}"
        raise UnusableIRIError(message)
    return URIRef(value).n3()


def literal_term(value: Any) -> str:
    """A literal as a SPARQL term, escaped by rdflib.

    `bool` before `int` on purpose: `isinstance(True, int)` is true in Python,
    so the obvious ordering types every boolean as an integer.
    """
    if isinstance(value, bool):
        return Literal(value).n3()
    if isinstance(value, int):
        return Literal(value, datatype=XSD.integer).n3()
    if isinstance(value, float):
        return Literal(value, datatype=XSD.decimal).n3()
    return Literal(str(value)).n3()


def term(value: Any, *, is_reference: bool) -> str:
    """An IRI term when `is_reference`, otherwise a literal."""
    return iri_term(str(value)) if is_reference else literal_term(value)


def datetime_term(moment: Any) -> str:
    """An `xsd:dateTime` literal from a datetime or an ISO string."""
    text = moment if isinstance(moment, str) else moment.isoformat()
    return Literal(text, datatype=XSD.dateTime).n3()


#: The prefixes the v3 graphs actually use, for turning a full IRI back into
#: the compact form the projections and the JSON-LD context work in.
#:
#: A deliberate subset of `scripts/v2/ontology_reader.PREFIXES`, which is the
#: authority — but which lives under `scripts/` and cannot be imported by
#: production code. `tests/v2/test_store_terms.py::test_the_compact_prefixes_are_a_subset_of_the_readers`
#: asserts the two agree, because a second copy of a prefix table is exactly
#: how a context ends up unable to expand the terms its own models emit (§3c).
COMPACT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("https://open-pulse.epfl.ch/ontology#", "pulse:"),
    ("http://schema.org/", "schema:"),
    ("http://www.w3.org/ns/org#", "org:"),
    ("http://www.w3.org/ns/prov#", "prov:"),
    ("http://www.w3.org/2006/time#", "time:"),
    ("http://www.w3.org/2002/07/owl#", "owl:"),
    ("http://www.wikidata.org/entity/", "wd:"),
    ("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdf:"),
)


def compact(iri: str) -> str:
    """`http://schema.org/name` -> `schema:name`, or the IRI unchanged."""
    for base, prefix in COMPACT_PREFIXES:
        if iri.startswith(base):
            return prefix + iri[len(base) :]
    return iri


def expand(curie: str) -> str:
    """The inverse: `schema:name` -> `http://schema.org/name`."""
    for base, prefix in COMPACT_PREFIXES:
        if curie.startswith(prefix):
            return base + curie[len(prefix) :]
    return curie


__all__ = [
    "COMPACT_PREFIXES",
    "FORBIDDEN_IRI_CHARACTERS",
    "UnusableIRIError",
    "compact",
    "datetime_term",
    "expand",
    "iri_term",
    "literal_term",
    "term",
]
