"""Tests for the RDF-star provenance writer.

`PROVENANCE_ARCHITECTURE.md` phase 5. Every value the unifier *chose* is a
derived fact, and this records which source it came from and by what rule:

    << <https://orcid.org/0000-...> schema:name "Jane Doe" >>
        prov:wasDerivedFrom <urn:pulse:output:run-b:github> ;
        pulse:observationKind "most-complete-source" ; ...

**Built against fixtures, deliberately and by decision.** The 119-run corpus
produces **zero** contested selections — every cross-run difference in it is
either `pulse:partOfRun` (per-run by design) or set accumulation — so there is
nothing real for a provenance writer to record until a second platform is
harvested. Waiting for GitLab was the alternative; building now against
purpose-made two-run conflicts was chosen. That means these fixtures *are* the
specification, so they are written to say what they mean rather than to pass.

What is asserted here is the statement text, which is checkable offline. That
the statements do what they claim against a real RDF-star store was verified
separately against `ghcr.io/oxigraph/oxigraph:0.4.11` — insert, upsert with a
bumped counter, the ontology's documented read-back query, and the prune — and
recorded in §3k of `REFACTOR_HANDOFF.md`. Neither the pinned rdflib (6.3.2) nor
pyshacl (0.28.1) can process a quoted triple, so there is no offline substitute
for that half.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from rdflib import Graph

from git_metadata_extractor.unify.cluster import Record
from git_metadata_extractor.unify.merge import (
    RULE_FIRST_STABLE,
    RULE_MOST_COMPLETE_SOURCE,
)
from git_metadata_extractor.unify.policy import default_policy
from git_metadata_extractor.unify.provenance import (
    FIRST_OBSERVED_ON,
    INSERT_CHUNK,
    OBSERVATION_COUNT,
    OBSERVATION_KIND,
    PROV_GRAPH,
    WAS_DERIVED_FROM,
    Annotation,
    ExistingAnnotation,
    annotations_for,
    build_statements,
    prune_statement,
    quoted_triple,
    same_as_edges,
)
from git_metadata_extractor.unify.runner import CANONICAL_GRAPH, unify_records

RUN_A = "urn:pulse:output:run-a:github"
RUN_B = "urn:pulse:output:run-b:github"
ORCID = "0000-0002-1825-0097"
ORCID_IRI = f"https://orcid.org/{ORCID}"
PROFILE = "urn:pulse:profile:github:jane"
NOW = datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc)
EARLIER = "2026-09-01T09:00:00+00:00"

EXPECTED_COUNT_ON_RECONFIRM = 5
EXPECTED_DELETE_STATEMENTS = 2
EXPECTED_INSERT_CHUNKS = 2


@pytest.fixture
def conflicting_runs() -> list[Record]:
    """Two runs that genuinely disagree — what the corpus never produces.

    Run A knows this person only by their GitHub handle and calls them "jane".
    Run B resolved their ORCID, holds an email as well, and calls them
    "Jane Doe". One shared `pulse:PlatformProfile` links the two records; the
    ORCID promotes the identity; and `schema:name` has to be *decided*, which
    is the only kind of value provenance records.
    """
    return [
        Record(
            iri="https://github.com/jane",
            entity_type="schema:Person",
            graph=RUN_A,
            properties={
                "schema:name": ["jane"],
                "pulse:hasProfile": [{"@id": PROFILE}],
            },
        ),
        Record(
            iri=ORCID_IRI,
            entity_type="schema:Person",
            graph=RUN_B,
            properties={
                "schema:name": ["Jane Doe"],
                "pulse:orcidIdentifier": [ORCID],
                "schema:email": ["jane@lab.ch"],
                "pulse:hasProfile": [{"@id": PROFILE}],
            },
        ),
    ]


def _annotations(records: list[Record]) -> list[Annotation]:
    merged, _report = unify_records(records)
    return annotations_for(merged)


def _statements(records: list[Record]) -> list[str]:
    merged, _report = unify_records(records)
    policy = default_policy()
    return build_statements(
        annotations_for(merged),
        same_as_edges(merged, policy),
        {},
        now=NOW.isoformat(),
    )


# --------------------------------------------------------------------------
# what gets recorded, and what deliberately does not
# --------------------------------------------------------------------------


def test_a_contested_value_is_recorded(conflicting_runs: list[Record]) -> None:
    annotations = _annotations(conflicting_runs)
    names = [item for item in annotations if item.prop == "schema:name"]

    assert len(names) == 1
    assert names[0].subject == ORCID_IRI
    assert names[0].value == "Jane Doe"
    assert names[0].kind == RULE_MOST_COMPLETE_SOURCE
    # The *output*, not the run: the ontology is specific that
    # `prov:wasDerivedFrom` names one platform's slice, because that is the
    # granularity which answers "which source said this".
    assert names[0].source_graph == RUN_B


def test_an_uncontested_value_is_not_recorded() -> None:
    """Derived-only. A single-candidate value is already attributed.

    It sits in exactly one named graph, so the graph *is* its provenance.
    Reifying it would multiply storage for what the substrate already holds —
    the "cheap corner" of the architecture's cost profile.
    """
    annotations = _annotations(
        [
            Record(
                iri=ORCID_IRI,
                entity_type="schema:Person",
                graph=RUN_A,
                properties={"schema:name": ["Jane Doe"], "pulse:orcidIdentifier": [ORCID]},
            ),
        ],
    )

    assert annotations == []


def test_a_unioned_value_is_not_recorded() -> None:
    """A union asserts what every source asserted; nothing was decided.

    Each triple keeps the named-graph attribution it arrived with, so there is
    no derivation to explain. This is the case the whole corpus consists of.
    """
    annotations = _annotations(
        [
            Record(
                iri="https://ror.org/02s376052",
                entity_type="org:Organization",
                graph=graph,
                properties={
                    "pulse:ror": ["https://ror.org/02s376052"],
                    "org:hasUnit": [{"@id": unit}],
                },
            )
            for graph, unit in (
                (RUN_A, "https://github.com/LTS5"),
                (RUN_B, "https://github.com/mmspg"),
            )
        ],
    )

    assert [item.prop for item in annotations] == []


# --------------------------------------------------------------------------
# the quoted triple has to name the triple canonical actually holds
# --------------------------------------------------------------------------


def test_a_literal_value_is_quoted_as_a_literal() -> None:
    triple = quoted_triple(
        Annotation(
            subject=ORCID_IRI,
            prop="schema:name",
            value="Jane Doe",
            is_reference=False,
            source_graph=RUN_B,
            kind=RULE_MOST_COMPLETE_SOURCE,
        ),
    )

    assert triple == f'<< <{ORCID_IRI}> <http://schema.org/name> "Jane Doe" >>'


def test_a_reference_value_is_quoted_as_an_iri() -> None:
    """Getting this wrong is silent.

    `<< s p "https://..." >>` and `<< s p <https://...> >>` are different
    triples, so an annotation with the wrong term form attaches to nothing —
    and nothing errors, because a quoted triple need not exist to be annotated.
    """
    triple = quoted_triple(
        Annotation(
            subject="https://github.com/acme/tool",
            prop="schema:license",
            value="https://spdx.org/licenses/MIT.html",
            is_reference=True,
            source_graph=RUN_A,
            kind=RULE_FIRST_STABLE,
        ),
    )

    assert triple.endswith("<https://spdx.org/licenses/MIT.html> >>")


def test_the_term_form_is_read_off_the_merged_entity() -> None:
    """Not guessed from the value, which for a URL is ambiguous.

    `merge` already decided the form when it rebuilt the node; the annotation
    has to agree with it rather than re-derive it.
    """
    records = [
        Record(
            iri="https://github.com/acme/tool",
            entity_type="schema:SoftwareSourceCode",
            graph=graph,
            properties={"schema:license": [{"@id": licence}]},
        )
        for graph, licence in (
            (RUN_A, "https://spdx.org/licenses/MIT.html"),
            (RUN_B, "https://spdx.org/licenses/GPL-3.0.html"),
        )
    ]
    licences = [a for a in _annotations(records) if a.prop == "schema:license"]

    assert len(licences) == 1
    assert licences[0].is_reference is True


@pytest.mark.parametrize(
    "value",
    [
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
    ],
)
def test_literals_round_trip_through_a_parser(value: str) -> None:
    """These values come out of arbitrary repositories.

    A `schema:name` holding a quote, a backslash, a newline or a brace would
    break the generated SPARQL — or, worse, alter it. Escaping is rdflib's
    `Literal.n3()` rather than anything hand-rolled, for exactly that reason.

    Asserted as a **round trip** rather than against expected text, because
    the text is not the contract and guessing it is how the first version of
    this test failed on three of four cases: rdflib wraps a value containing a
    newline in Turtle long-quotes rather than escaping it, and leaves a tab
    raw inside ordinary quotes. Both are valid and neither is what a
    hand-written expectation predicts. All eight of these were also verified
    round-tripping through a live Oxigraph.
    """
    term = quoted_triple(
        Annotation(
            subject=ORCID_IRI,
            prop="schema:name",
            value=value,
            is_reference=False,
            source_graph=RUN_A,
            kind=RULE_FIRST_STABLE,
        ),
    ).removeprefix(f"<< <{ORCID_IRI}> <http://schema.org/name> ").removesuffix(" >>")

    # Parse the serialised term back as Turtle — the one thing the pinned
    # rdflib *can* do with these, since only the `<< >>` wrapper is beyond it.
    parsed = Graph().parse(
        data=f"<urn:s> <urn:p> {term} .",
        format="turtle",
    )
    objects = [str(obj) for _s, _p, obj in parsed]

    assert objects == [value]


@pytest.mark.parametrize(
    "bad",
    [
        "https://example.org/a>b",
        "https://example.org/a b",
        'https://example.org/a"b',
        "https://example.org/a\nb",
    ],
)
def test_an_unusable_iri_is_refused_not_mangled(bad: str) -> None:
    """`URIRef.n3()` does not escape, so a bad IRI would corrupt the statement.

    Refusing is the right failure: a dropped annotation is recoverable by
    re-running unification, a silently altered one is not.
    """
    annotation = Annotation(
        subject=bad,
        prop="schema:name",
        value="x",
        is_reference=False,
        source_graph=RUN_A,
        kind=RULE_FIRST_STABLE,
    )

    with pytest.raises(ValueError, match="not usable as a SPARQL term"):
        quoted_triple(annotation)


# --------------------------------------------------------------------------
# the statements
# --------------------------------------------------------------------------


def test_the_pass_clears_only_what_it_restates(
    conflicting_runs: list[Record],
) -> None:
    """Scoped deletes, not a `DROP GRAPH`.

    `graph:prov` is also where a later phase would put other provenance, and
    dropping the graph to rewrite the annotations would take that with it. So
    the clears are bounded by `isTRIPLE(?t)` for the star annotations and by
    predicate for the `sameAs` edges.
    """
    statements = _statements(conflicting_runs)
    deletes = [s for s in statements if s.lstrip().startswith(("DELETE", "PREFIX")) and "DELETE" in s]

    assert len(deletes) == EXPECTED_DELETE_STATEMENTS
    assert any("isTRIPLE(?t)" in s for s in deletes)
    assert any("samePersonAs" in s for s in deletes)
    assert not any("DROP" in s for s in statements)


def test_the_annotation_carries_the_full_vocabulary(
    conflicting_runs: list[Record],
) -> None:
    inserts = "\n".join(s for s in _statements(conflicting_runs) if "INSERT DATA" in s)

    for predicate in (
        WAS_DERIVED_FROM,
        OBSERVATION_KIND,
        FIRST_OBSERVED_ON,
        OBSERVATION_COUNT,
    ):
        assert f"<{predicate}>" in inserts
    assert f"GRAPH <{PROV_GRAPH}>" in inserts
    assert RUN_B in inserts


def test_a_first_observation_counts_one(conflicting_runs: list[Record]) -> None:
    inserts = "\n".join(s for s in _statements(conflicting_runs) if "INSERT DATA" in s)

    assert f'<{OBSERVATION_COUNT}> "1"^^' in inserts
    # First and last coincide on a first sighting, and both are this pass.
    assert inserts.count(f'"{NOW.isoformat()}"') >= 1


def test_a_reconfirmation_bumps_the_counter_and_keeps_the_first_date(
    conflicting_runs: list[Record],
) -> None:
    """`observationCount` and `firstObservedOn` are history.

    The writer clears and restates rather than issuing a per-triple upsert —
    hundreds of annotations would be hundreds of round trips — so the history
    has to be carried forward in Python from `read_existing`. Without that the
    counter resets to 1 on every pass and the field becomes a lie.
    """
    merged, _report = unify_records(conflicting_runs)
    annotations = annotations_for(merged)
    existing = {
        annotations[0].key: ExistingAnnotation(count=4, first_observed_on=EARLIER),
    }
    statements = build_statements(annotations, [], existing, now=NOW.isoformat())
    inserts = "\n".join(s for s in statements if "INSERT DATA" in s)

    assert f'<{OBSERVATION_COUNT}> "{EXPECTED_COUNT_ON_RECONFIRM}"^^' in inserts
    assert f'<{FIRST_OBSERVED_ON}> "{EARLIER}"' in inserts
    assert f'<{OBSERVATION_COUNT}> "1"^^' not in inserts


def test_no_confidence_is_fabricated(conflicting_runs: list[Record]) -> None:
    """`pulse:observationConfidence` is declared and left empty on purpose.

    The only thing available to derive it from is the selection rule, which
    `pulse:observationKind` already states — so a number here would be the
    same information dressed up as a measurement.
    """
    statements = "\n".join(_statements(conflicting_runs))

    assert "observationConfidence" not in statements


# --------------------------------------------------------------------------
# the sameAs edges, which are plain triples and live here
# --------------------------------------------------------------------------


def test_the_same_as_edge_is_written_as_a_plain_triple(
    conflicting_runs: list[Record],
) -> None:
    """Not a quoted-triple annotation — the ontology says so explicitly.

    And it cannot go in `graph:canonical`: `PersonShape` is `sh:closed` and
    ignores only `( rdf:type owl:sameAs )`, so `pulse:samePersonAs` — a
    *subproperty* — is rejected there.
    """
    merged, _report = unify_records(conflicting_runs)
    edges = same_as_edges(merged, default_policy())

    assert edges == [
        (
            ORCID_IRI,
            "https://open-pulse.epfl.ch/ontology#samePersonAs",
            "https://github.com/jane",
        ),
    ]
    inserts = "\n".join(
        s for s in _statements(conflicting_runs) if "INSERT DATA" in s
    )
    assert f"<{ORCID_IRI}> <https://open-pulse.epfl.ch/ontology#samePersonAs>" in inserts
    assert "<< <https://orcid.org" in inserts  # the annotations are still quoted


def test_no_alias_means_no_same_as_edge() -> None:
    merged, _report = unify_records(
        [
            Record(
                iri=ORCID_IRI,
                entity_type="schema:Person",
                graph=RUN_A,
                properties={"schema:name": ["Jane Doe"]},
            ),
        ],
    )

    assert same_as_edges(merged, default_policy()) == []


# --------------------------------------------------------------------------
# pruning
# --------------------------------------------------------------------------


def test_the_prune_joins_against_the_canonical_graph() -> None:
    """A value that stops winning leaves an annotation for a triple not there.

    The ontology's own read-back query joins `graph:canonical` to `graph:prov`,
    so such a record is unreachable and only misleads someone reading the graph
    directly. Pruning it discards that value's observation history, which is
    the cheap-corner trade the cost profile chooses.
    """
    statement = prune_statement(CANONICAL_GRAPH)

    assert f"GRAPH <{PROV_GRAPH}>" in statement
    assert f"GRAPH <{CANONICAL_GRAPH}>" in statement
    assert "FILTER NOT EXISTS" in statement
    assert "TRIPLE(?s, ?p, ?o)" in statement
    assert "isTRIPLE(?t)" in statement


def test_chunking_bounds_the_statement_size() -> None:
    """One request per 200 annotations, so a failure names a small batch."""
    annotations = [
        Annotation(
            subject=f"https://github.com/user{index}",
            prop="schema:name",
            value=f"name-{index}",
            is_reference=False,
            source_graph=RUN_A,
            kind=RULE_FIRST_STABLE,
        )
        for index in range(INSERT_CHUNK + 5)
    ]
    statements = build_statements(annotations, [], {}, now=NOW.isoformat())
    inserts = [s for s in statements if "INSERT DATA" in s]

    assert len(inserts) == EXPECTED_INSERT_CHUNKS


def _values(statements: list[str]) -> str:
    return "\n".join(statements)


def test_integers_keep_their_datatype() -> None:
    """A count quoted as a string would not compare or sort in SPARQL."""
    annotations = [
        Annotation(
            subject="https://github.com/acme/tool",
            prop="pulse:repositoryStars",
            value=1497,
            is_reference=False,
            source_graph=RUN_A,
            kind=RULE_FIRST_STABLE,
        ),
    ]
    text = _values(build_statements(annotations, [], {}, now=NOW.isoformat()))

    assert '"1497"^^' in text
    assert "XMLSchema#integer" in text


def _first_insert(records: list[Record]) -> str:
    return next(s for s in _statements(records) if "INSERT DATA" in s)


def test_the_statement_is_syntactically_parseable_sparql(
    conflicting_runs: list[Record],
) -> None:
    """A cheap structural check, since rdflib cannot parse RDF-star.

    Balanced `<<`/`>>` and braces catch the mistakes a template makes; the
    real proof is the live round trip against Oxigraph, recorded in the
    handoff.
    """
    statement = _first_insert(conflicting_runs)

    assert statement.count("<<") == statement.count(">>")
    assert statement.count("{") == statement.count("}")
    assert statement.rstrip().endswith("}")


def test_annotations_carry_no_per_run_property(conflicting_runs: list[Record]) -> None:
    """`pulse:partOfRun` never reaches canonical, so it is never annotated.

    Nine of the twelve cross-run differences in the real corpus are that one
    property. If it leaked into canonical it would also become the bulk of the
    provenance graph, all of it noise.
    """
    annotations = _annotations(conflicting_runs)

    assert all(item.prop != "pulse:partOfRun" for item in annotations)


def test_annotations_for_is_pure(conflicting_runs: list[Record]) -> None:
    """Building the annotations must not disturb the entities they describe."""
    merged, _report = unify_records(conflicting_runs)
    before: list[dict[str, Any]] = [dict(entity.properties) for entity in merged]

    annotations_for(merged)

    assert [dict(entity.properties) for entity in merged] == before


def test_a_surviving_conflict_annotates_the_remapped_value() -> None:
    """The annotation has to quote the triple `graph:canonical` actually holds.

    `merge` records what it chose *before* `remap_entities` runs, so a
    selection whose winner is a reference can name an IRI the remap then
    replaces. A quoted triple need not exist to be annotated, so a stale
    winner attaches the annotation to nothing and nothing errors.

    Found on a live store: the prune deleted the mismatched annotation on
    every pass, so a second run reported "1 new, 1 reconfirmed" forever
    instead of a counter that climbs. The visible symptom was a number that
    would not move.

    The conflict here genuinely survives — two *different* organizations, one
    of which is renamed — unlike the dissolving case in the next test.
    """
    epfl_github = "https://github.com/epfl"
    epfl_ror = "https://ror.org/02s376052"
    other = "https://github.com/other"
    org_profile = "urn:pulse:org-profile:github:epfl"
    membership = f"https://github.com/jane__{epfl_github}"

    merged, report = unify_records(
        [
            # One organization, named two ways across runs -> renamed to the ROR.
            Record(
                iri=epfl_github,
                entity_type="org:Organization",
                graph=RUN_A,
                properties={"pulse:hasOrganizationProfile": [{"@id": org_profile}]},
            ),
            Record(
                iri=epfl_ror,
                entity_type="org:Organization",
                graph=RUN_B,
                properties={
                    "pulse:ror": [epfl_ror],
                    "pulse:hasOrganizationProfile": [{"@id": org_profile}],
                },
            ),
            # A genuinely different organization, not renamed.
            Record(
                iri=other,
                entity_type="org:Organization",
                graph=RUN_B,
                properties={"schema:name": ["Other Lab"]},
            ),
            # A membership the two runs disagree about: `org:organization` is
            # capped, so it is a SELECT between two distinct targets.
            Record(
                iri=membership,
                entity_type="org:Membership",
                graph=RUN_A,
                properties={"org:organization": [{"@id": epfl_github}]},
            ),
            Record(
                iri=membership,
                entity_type="org:Membership",
                graph=RUN_B,
                properties={"org:organization": [{"@id": other}]},
            ),
        ],
    )

    assert report.remapped_references == 1
    annotation = next(
        item for item in annotations_for(merged) if item.prop == "org:organization"
    )
    assert annotation.is_reference is True
    # Whichever won, it must be an IRI that survived the remap — never the
    # pre-canonical `github.com/epfl`.
    assert annotation.value != epfl_github
    assert annotation.value in {epfl_ror, other}

    # And the quoted triple agrees with the node in canonical.
    entity = next(item for item in merged if item.entity_type == "org:Membership")
    assert entity.properties["org:organization"] == {"@id": annotation.value}


def test_a_conflict_that_identity_resolution_dissolves_is_not_recorded() -> None:
    """Two runs naming one entity differently is not a disagreement.

    The profile's `pulse:profileOf` looked contested — run A said
    `github.com/jane`, run B said the ORCID — but both are the *same person*,
    which is exactly what the unifier just established. Once the remap applies,
    the loser equals the winner.

    Recording it would print a contested value whose winner and loser are
    identical (which is how it was noticed) and write a provenance annotation
    for a decision nobody made.
    """
    profile = "urn:pulse:profile:github:jane"
    merged, report = unify_records(
        [
            Record(
                iri="https://github.com/jane",
                entity_type="schema:Person",
                graph=RUN_A,
                properties={"pulse:hasProfile": [{"@id": profile}]},
            ),
            Record(
                iri=ORCID_IRI,
                entity_type="schema:Person",
                graph=RUN_B,
                properties={
                    "pulse:orcidIdentifier": [ORCID],
                    "pulse:hasProfile": [{"@id": profile}],
                },
            ),
            Record(
                iri=profile,
                entity_type="pulse:PlatformProfile",
                graph=RUN_A,
                properties={"pulse:profileOf": [{"@id": "https://github.com/jane"}]},
            ),
            Record(
                iri=profile,
                entity_type="pulse:PlatformProfile",
                graph=RUN_B,
                properties={"pulse:profileOf": [{"@id": ORCID_IRI}]},
            ),
        ],
    )

    assert report.contested == []
    assert annotations_for(merged) == []
    # The value is still there and still correct; only the *record of a
    # decision* is gone, because there was no decision.
    profile_entity = next(entity for entity in merged if entity.iri == profile)
    assert profile_entity.properties["pulse:profileOf"] == {"@id": ORCID_IRI}
