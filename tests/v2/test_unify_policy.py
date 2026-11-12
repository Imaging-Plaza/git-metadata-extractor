"""The unifier's policy, checked against the real canonical shapes.

`unify/policy.py` hand-classifies 45 properties the shapes cannot classify —
`sh:maxCount 1` is absent on `schema:name` and on `pulse:owns` alike, so the
ontology cannot say which of them a union would corrupt. That table is the
direct cost of the accepted-as-is cardinality decision (§3c of
`REFACTOR_HANDOFF.md`), and a hand-written table against a versioned ontology
is exactly the thing that rots quietly.

Four guards, all reading the pinned TTL rather than a copy of it:

- **No always-capped property may be `UNION`.** This direction the shapes *can*
  settle: `sh:maxCount 1` means a union writes a graph the closed canonical
  shapes reject — and reject silently, because the SHACL gate is warning-only.
- **Every uncapped property must be classified**, and no entry may name a
  property the shapes dropped. A submodule bump either way should fail here
  rather than default to `SELECT` and surface as a missing value months later.
- **A shape-dependent cap is honoured per type.** The first version of the
  first guard treated "capped anywhere" as capped and declared a correct table
  wrong: `schema:author` is `sh:maxCount 1` on `pulse:Contribution` and
  unbounded on articles and repositories. It is the only such property today,
  which is exactly why it needs pinning — a second one would be handled by
  luck.
- **The generated cap sets agree with the shapes**, so a `schema/generated/`
  that was not regenerated after a shapes edit fails here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdflib import OWL, RDF, RDFS, SKOS, Graph

from git_metadata_extractor.unify.policy import (
    PER_RUN_PROPERTIES,
    RESOLVERS,
    UNCAPPED_DISPOSITIONS,
    Disposition,
    default_policy,
    single_valued_for,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = REPO_ROOT / "vendor" / "open-pulse-ontology" / "src" / "ontology"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "v2"))

pytestmark = pytest.mark.skipif(
    not ONTOLOGY.is_dir(),
    reason="ontology submodule not checked out (git submodule update --init)",
)


def _caps_per_shape() -> dict[str, dict[str, bool]]:
    """`property -> {target class: is it sh:maxCount 1 there}`, from the TTL."""
    from ontology_reader import read_shapes  # noqa: PLC0415

    out: dict[str, dict[str, bool]] = {}
    for shape in read_shapes(ONTOLOGY / "ontology-shapes-canonical.ttl"):
        for prop in shape.properties:
            out.setdefault(prop.path, {})[shape.target_class] = prop.max_count == 1
    return out


def _canonical_properties() -> tuple[set[str], set[str]]:
    """(capped-everywhere, uncapped-somewhere) canonical property paths.

    The split has to be per shape, not global. Exactly one canonical property
    is shape-dependent — `schema:author`, capped on `pulse:Contribution` and
    unbounded on articles and repositories — and collapsing that to a single
    boolean is how the first version of this test declared a correct table
    wrong.
    """
    per_shape = _caps_per_shape()
    capped = {prop for prop, caps in per_shape.items() if all(caps.values())}
    uncapped = {prop for prop, caps in per_shape.items() if not all(caps.values())}
    return capped, uncapped


def test_no_always_capped_property_is_unioned() -> None:
    """A union of a `sh:maxCount 1` property is a closed-shape violation.

    And a silent one: `shacl_gate` reports rather than rejects, so the graph
    would ship. This is the direction the ontology can decide, so it decides —
    for the properties every shape caps. The shape-dependent one is covered by
    `test_a_shape_dependent_cap_is_honoured_per_type`.
    """
    capped, _uncapped = _canonical_properties()

    offenders = sorted(
        prop
        for prop, disposition in UNCAPPED_DISPOSITIONS.items()
        if prop in capped and disposition is Disposition.UNION
    )
    assert not offenders, f"sh:maxCount 1 everywhere but marked UNION: {offenders}"


def test_a_shape_dependent_cap_is_honoured_per_type() -> None:
    """`schema:author`: one author on a Contribution, many on an Article.

    The table marks it UNION, which is right for articles and repositories and
    wrong for contributions — so the per-type cap set has to override it. This
    is the case that proved a per-property table cannot be the whole answer,
    and it is pinned here because it is the only one in the ontology today: a
    second such property would otherwise be handled by luck.
    """
    per_shape = _caps_per_shape()
    caps = per_shape["schema:author"]
    assert caps["pulse:Contribution"] is True
    assert caps["schema:ScholarlyArticle"] is False

    assert UNCAPPED_DISPOSITIONS["schema:author"] is Disposition.UNION
    assert "schema:author" in single_valued_for("pulse:Contribution")
    assert "schema:author" not in single_valued_for("schema:ScholarlyArticle")

    policy = default_policy()
    assert policy.disposition("schema:author", capped=True) is Disposition.SELECT
    assert policy.disposition("schema:author", capped=False) is Disposition.UNION


def test_the_generated_cap_sets_match_the_shapes() -> None:
    """The generated tables are the unifier's source of truth for cardinality.

    They come from the same reader as this test, so agreement is expected —
    the point is that a stale `schema/generated/` (someone edited the shapes
    and skipped `just ontology-models-generate`) fails here rather than
    producing a graph that quietly violates a cap.
    """
    per_shape = _caps_per_shape()
    for prop, caps in per_shape.items():
        for target_class, is_capped in caps.items():
            generated = prop in single_valued_for(target_class)
            assert generated == is_capped, (
                f"{target_class} {prop}: shapes say capped={is_capped}, "
                f"generated says {generated}"
            )


def test_every_uncapped_property_is_classified() -> None:
    """An unclassified property defaults to SELECT, which is a guess.

    Failing here on a submodule bump is the point: the shapes gained a
    property, and only a person can say whether accumulating it across runs is
    truth or corruption.
    """
    _capped, uncapped = _canonical_properties()

    unclassified = sorted(uncapped - set(UNCAPPED_DISPOSITIONS) - PER_RUN_PROPERTIES)
    assert not unclassified, f"no disposition for {unclassified}"


def test_the_table_does_not_classify_properties_the_shapes_dropped() -> None:
    """The mirror: an entry for a property no shape declares is dead weight.

    Kept as a separate assertion because the failure means the opposite thing —
    a property was *removed* upstream, and the table is now describing a
    vocabulary that no longer exists.
    """
    capped, uncapped = _canonical_properties()

    stale = sorted(set(UNCAPPED_DISPOSITIONS) - uncapped - capped)
    assert not stale, f"classified but no canonical shape declares it: {stale}"


def test_a_cap_overrides_the_table_at_runtime() -> None:
    """The runtime floor under `test_no_capped_property_is_unioned`.

    The test above guards the shipped table; this guards a table someone edits
    without running it. A capped property forced to SELECT is a degradation; a
    capped property unioned is invalid data.
    """
    policy = default_policy()
    policy.dispositions["pulse:platform"] = Disposition.UNION

    assert policy.disposition("pulse:platform", capped=True) is Disposition.SELECT
    assert policy.disposition("pulse:platform", capped=False) is Disposition.UNION


def test_part_of_run_never_reaches_canonical() -> None:
    """The substrate's anchor is per-run by design, not a value to merge.

    Nine of the twelve cross-run property differences in the corpus are this
    one property. Treating them as conflicts would make the unifier's report
    almost entirely noise, and carrying the winner into canonical would assert
    that an entity belongs to one arbitrary run's slice.
    """
    policy = default_policy()

    assert policy.disposition("pulse:partOfRun") is Disposition.PER_RUN
    # And it wins over an explicit entry, since the table is not where the
    # substrate/canonical boundary is decided.
    policy.dispositions["pulse:partOfRun"] = Disposition.UNION
    assert policy.disposition("pulse:partOfRun") is Disposition.PER_RUN


# --------------------------------------------------------------------------
# the per-type resolvers
# --------------------------------------------------------------------------


def test_match_keys_are_properties_the_shapes_declare() -> None:
    """A match key the ontology does not have never matches anything.

    It also never errors — clustering just falls back to IRI equality, so the
    unifier quietly stops deduplicating and reports success.
    """
    capped, uncapped = _canonical_properties()
    declared = capped | uncapped

    for entity_type, resolver in RESOLVERS.items():
        unknown = sorted(set(resolver.match_keys) - declared)
        assert not unknown, f"{entity_type} matches on undeclared {unknown}"
        unknown_ids = sorted(set(resolver.id_priority) - declared)
        assert not unknown_ids, f"{entity_type} promotes on undeclared {unknown_ids}"


def test_the_global_identifier_is_always_the_first_match_key() -> None:
    """Precision order is not cosmetic: it is what blocks a bad merge.

    Clustering trusts an earlier key absolutely and lets it veto a later one.
    If `schema:email` came before `pulse:orcidIdentifier`, two people sharing a
    shared-inbox address would merge and the ORCIDs that prove them distinct
    would never be consulted.
    """
    for entity_type, resolver in RESOLVERS.items():
        if not resolver.id_priority:
            continue
        assert resolver.match_keys[0] == resolver.id_priority[0], (
            f"{entity_type}: {resolver.id_priority[0]} must be matched first"
        )


def test_the_same_as_predicates_exist_in_the_ontology() -> None:
    """`pulse:sameOrganizationAs` comes from ontology patch 02, now upstream.

    Which had no consumer until the unifier: it was added for the id migration
    and sat unused. If the patch is ever dropped as unused, this fails — and so
    it does if patch 09 is, which makes both `skos:exactMatch` subproperties
    rather than `owl:sameAs` ones.

    Parsed, not grepped. `graph:prov` is never SHACL-validated, so nothing else
    checks these predicates, and a substring check passed with the
    `pulse:samePersonAs` declaration deleted: the `sameOrganizationAs`
    definition mentions it by name.
    """
    graph = Graph().parse(
        ONTOLOGY / "ontology-definitions-provenance.ttl",
        format="turtle",
    )
    for resolver in RESOLVERS.values():
        if not resolver.same_as:
            continue
        predicate = graph.namespace_manager.expand_curie(resolver.same_as)
        declared = set(graph.objects(predicate, RDF.type))
        assert declared & {RDF.Property, OWL.ObjectProperty}, (
            f"{resolver.same_as} not declared"
        )
        assert (predicate, RDFS.subPropertyOf, SKOS.exactMatch) in graph, (
            f"{resolver.same_as} is not a skos:exactMatch subproperty (patch 09)"
        )
