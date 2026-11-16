"""Tests for the store-side unifier: clustering, merging, remapping.

`PROVENANCE_ARCHITECTURE.md` phase 4. Its stated exit criterion is "two
profiles with a shared ORCID collapse to one canonical Person across two runs",
and the first thing measuring showed is that **the criterion as written is
already met by extraction**: substrate ids come from
`canonicalization/id_resolution.py`, which resolves ORCID per request, so two
runs seeing the same ORCID already agree on the IRI and the unifier has nothing
to collapse.

The case that actually needs a unifier is one step harder and is what
`test_a_github_person_and_an_orcid_person_collapse` pins: run A knows a person
only by their GitHub handle, run B resolves their ORCID. Two different IRIs,
one shared `pulse:PlatformProfile` — which is precisely what the profile model
exists for, per the output-shape note in `AGENTS.md`.

**Why so much of this is fixtures rather than corpus.** Measured over the 119
real substrates: 17 entities appear in more than one run, and of their twelve
differing properties nine are `pulse:partOfRun` (per-run by design) and three
are set accumulation. **Not one genuine value conflict.** So `SELECT` — the
whole value-selection path — has no corpus coverage available, the same blind
spot Phase A hit with the RAG providers. It is tested against purpose-built
records and that is said out loud rather than implied by a green run.
"""

from __future__ import annotations

from typing import Any

import pytest

from git_metadata_extractor.unify.cluster import (
    Record,
    cluster_records,
    group_by_type,
)
from git_metadata_extractor.unify.merge import (
    RULE_FIRST_STABLE,
    RULE_MOST_COMPLETE_SOURCE,
    RULE_ONLY_CANDIDATE,
    contested_selections,
    merge_cluster,
)
from git_metadata_extractor.unify.policy import RESOLVERS, default_policy
from git_metadata_extractor.unify.provenance import same_as_edges
from git_metadata_extractor.unify.remap import (
    alias_map,
    dangling_references,
    remap_entities,
    stale_references,
)
from git_metadata_extractor.unify.runner import (
    CANONICAL_GRAPH,
    canonical_document,
    unify_records,
)

RUN_A = "urn:pulse:output:run-a:github"
RUN_B = "urn:pulse:output:run-b:github"
ORCID = "0000-0002-1825-0097"
ORCID_IRI = f"https://orcid.org/{ORCID}"
PROFILE = "urn:pulse:profile:github:jane"
ORG_PROFILE = "urn:pulse:org-profile:github:epfl"

EXPECTED_TWO = 2


def _person(
    iri: str,
    graph: str,
    **props: Any,
) -> Record:
    return Record(
        iri=iri,
        entity_type="schema:Person",
        graph=graph,
        properties=dict(props),
    )


def _by_iri(entities: list[Any]) -> dict[str, Any]:
    return {entity.iri: entity for entity in entities}


# --------------------------------------------------------------------------
# clustering
# --------------------------------------------------------------------------


def test_the_same_iri_in_two_runs_is_one_entity() -> None:
    """The common case, and the one extraction already produces.

    Substrate ids are resolved per request, so two runs that saw the same
    evidence agree on the IRI. Clustering has to accept that unconditionally —
    before any match key is consulted — or the unifier would second-guess a
    decision it has no more information about.
    """
    clusters = cluster_records(
        [
            _person(ORCID_IRI, RUN_A, **{"schema:name": ["Jane Doe"]}),
            _person(ORCID_IRI, RUN_B, **{"schema:name": ["Jane Doe"]}),
        ],
        RESOLVERS["schema:Person"],
    )

    assert len(clusters) == 1
    assert clusters[0].graphs == [RUN_A, RUN_B]


def test_a_github_person_and_an_orcid_person_collapse() -> None:
    """Phase 4's real exit criterion.

    Run A knows this person only by a GitHub handle, so extraction named them
    `https://github.com/jane`. Run B resolved their ORCID, so extraction named
    them `https://orcid.org/...`. Two IRIs, one person — and the only thing
    connecting them is the shared `pulse:PlatformProfile`, which is exactly the
    linkage the profile model was introduced for.
    """
    clusters = cluster_records(
        [
            _person(
                "https://github.com/jane",
                RUN_A,
                **{"pulse:hasProfile": [{"@id": PROFILE}]},
            ),
            _person(
                ORCID_IRI,
                RUN_B,
                **{
                    "pulse:orcidIdentifier": [ORCID],
                    "pulse:hasProfile": [{"@id": PROFILE}],
                },
            ),
        ],
        RESOLVERS["schema:Person"],
    )

    assert len(clusters) == 1
    assert sorted(clusters[0].iris) == ["https://github.com/jane", ORCID_IRI]


def test_a_stronger_key_vetoes_a_weaker_match() -> None:
    """Two people sharing an inbox must not merge.

    `schema:email` clusters records nothing more precise has separated — but
    two *different* ORCIDs are proof of distinctness, and ORCID is consulted
    first. Without the veto, a shared `info@lab.ch` fuses two researchers, and
    a dedup that invents identities is worse than none.
    """
    clusters = cluster_records(
        [
            _person(
                "https://orcid.org/0000-0002-1825-0097",
                RUN_A,
                **{
                    "pulse:orcidIdentifier": ["0000-0002-1825-0097"],
                    "schema:email": ["info@lab.ch"],
                },
            ),
            _person(
                "https://orcid.org/0000-0003-0426-6634",
                RUN_B,
                **{
                    "pulse:orcidIdentifier": ["0000-0003-0426-6634"],
                    "schema:email": ["info@lab.ch"],
                },
            ),
        ],
        RESOLVERS["schema:Person"],
    )

    assert len(clusters) == EXPECTED_TWO


def test_a_missing_stronger_key_is_not_a_disagreement() -> None:
    """Absence is not evidence of difference.

    One record carrying an ORCID and another carrying none says nothing about
    whether they are the same person. Treating that as a veto would refuse
    every merge that *adds* information — which is the only kind worth making,
    and the exact case above.
    """
    clusters = cluster_records(
        [
            _person("https://github.com/jane", RUN_A, **{"schema:email": ["j@lab.ch"]}),
            _person(
                ORCID_IRI,
                RUN_B,
                **{"pulse:orcidIdentifier": [ORCID], "schema:email": ["j@lab.ch"]},
            ),
        ],
        RESOLVERS["schema:Person"],
    )

    assert len(clusters) == 1


def test_group_by_type_splits_before_resolvers_run() -> None:
    """Resolvers are per type; a Person and an Organization never cluster."""
    grouped = group_by_type(
        [
            _person("https://github.com/jane", RUN_A),
            Record(iri="https://ror.org/02s376052", entity_type="org:Organization", graph=RUN_A),
        ],
    )

    assert set(grouped) == {"schema:Person", "org:Organization"}


# --------------------------------------------------------------------------
# merging - naming the canonical entity
# --------------------------------------------------------------------------


def test_the_canonical_iri_is_promoted_to_the_global_identifier() -> None:
    """`ORCID → ROR → DOI → a source IRI`, the same order as per-request.

    Deliberately identical to `canonicalization/id_resolution.py`: extraction
    keeps its own resolution under the decision recorded for this phase, so the
    two agree wherever they saw the same evidence. `owl:sameAs` carries the
    rest.
    """
    cluster = cluster_records(
        [
            _person(
                "https://github.com/jane",
                RUN_A,
                **{"pulse:hasProfile": [{"@id": PROFILE}]},
            ),
            _person(
                ORCID_IRI,
                RUN_B,
                **{
                    "pulse:orcidIdentifier": [ORCID],
                    "pulse:hasProfile": [{"@id": PROFILE}],
                },
            ),
        ],
        RESOLVERS["schema:Person"],
    )[0]
    merged = merge_cluster(cluster, default_policy())

    assert merged.iri == ORCID_IRI
    assert merged.aliases == ("https://github.com/jane",)


def test_a_urn_stub_never_wins_the_name() -> None:
    """`urn:pulse:` is what extraction falls back to knowing nothing.

    So when a cluster has no global identifier to promote to, any real URL in
    it is a better canonical name than a synthetic one — and the choice has to
    be deterministic, or two unifier runs over one store disagree.
    """
    cluster = cluster_records(
        [
            _person("urn:pulse:repo-author:acme/acme", RUN_A, **{"schema:email": ["a@b.ch"]}),
            _person("https://github.com/acme", RUN_B, **{"schema:email": ["a@b.ch"]}),
        ],
        RESOLVERS["schema:Person"],
    )[0]
    merged = merge_cluster(cluster, default_policy())

    assert merged.iri == "https://github.com/acme"
    assert merged.aliases == ("urn:pulse:repo-author:acme/acme",)


def test_the_alias_is_not_on_the_canonical_node() -> None:
    """`pulse:sameOrganizationAs` goes to `graph:prov`, not to canonical.

    Both because the ontology says so ("Plain triple in graph:prov, not a
    quoted-triple annotation") and because `OrganizationShape` is `sh:closed`
    and ignores only `( rdf:type owl:sameAs )` — a *subproperty* of
    `owl:sameAs` is rejected. `merge` emitted it on the node until the
    provenance writer existed, and the canonical graph still validated at 0
    violations, because the corpus never produced a rename.
    """
    policy = default_policy()
    cluster = cluster_records(
        [
            Record(
                iri="https://github.com/epfl",
                entity_type="org:Organization",
                graph=RUN_A,
                properties={"pulse:hasOrganizationProfile": [{"@id": "urn:pulse:org-profile:github:epfl"}]},
            ),
            Record(
                iri="https://ror.org/02s376052",
                entity_type="org:Organization",
                graph=RUN_B,
                properties={
                    "pulse:ror": ["https://ror.org/02s376052"],
                    "pulse:hasOrganizationProfile": [{"@id": "urn:pulse:org-profile:github:epfl"}],
                },
            ),
        ],
        RESOLVERS["org:Organization"],
    )[0]
    merged = merge_cluster(cluster, policy)
    node = merged.as_jsonld()

    assert node["@id"] == "https://ror.org/02s376052"
    assert "pulse:sameOrganizationAs" not in node
    # The link is still recorded — it just lives in the provenance graph.
    assert merged.aliases == ("https://github.com/epfl",)
    assert same_as_edges([merged], policy) == [
        (
            "https://ror.org/02s376052",
            "https://open-pulse.epfl.ch/ontology#sameOrganizationAs",
            "https://github.com/epfl",
        ),
    ]


# --------------------------------------------------------------------------
# merging - union vs select
# --------------------------------------------------------------------------


def test_set_valued_edges_accumulate_across_runs() -> None:
    """Where the corpus says the value actually is.

    EPFL's units are scattered across ten runs and only the union knows it has
    ten. Neither run is wrong and neither replaces the other, so there is
    nothing to select and nothing to reify — the named graphs already attribute
    each edge.
    """
    cluster = cluster_records(
        [
            Record(
                iri="https://ror.org/02s376052",
                entity_type="org:Organization",
                graph=RUN_A,
                properties={"org:hasUnit": [{"@id": "https://github.com/LTS5"}]},
            ),
            Record(
                iri="https://ror.org/02s376052",
                entity_type="org:Organization",
                graph=RUN_B,
                properties={"org:hasUnit": [{"@id": "https://github.com/mmspg"}]},
            ),
        ],
        RESOLVERS["org:Organization"],
    )[0]
    merged = merge_cluster(cluster, default_policy())

    assert merged.properties["org:hasUnit"] == [
        {"@id": "https://github.com/LTS5"},
        {"@id": "https://github.com/mmspg"},
    ]
    assert not contested_selections([merged])


def test_a_contested_single_value_picks_the_most_complete_source() -> None:
    """The source that knew the most is the likeliest to be right.

    `schema:name` is a list in the shapes and one fact in the world, so two
    runs disagreeing is a decision, not an accumulation. Unioning it would
    produce a person with two names — which validates, which is the point.
    """
    cluster = cluster_records(
        [
            _person(
                "https://github.com/jane",
                RUN_A,
                **{
                    "schema:name": ["jane"],
                    "pulse:hasProfile": [{"@id": PROFILE}],
                },
            ),
            _person(
                ORCID_IRI,
                RUN_B,
                **{
                    "schema:name": ["Jane Doe"],
                    "pulse:orcidIdentifier": [ORCID],
                    "schema:email": ["jane@lab.ch"],
                    "pulse:hasProfile": [{"@id": PROFILE}],
                },
            ),
        ],
        RESOLVERS["schema:Person"],
    )[0]
    merged = merge_cluster(cluster, default_policy())

    assert merged.properties["schema:name"] == ["Jane Doe"]
    selection = next(s for s in merged.selections if s.prop == "schema:name")
    assert selection.winner == "Jane Doe"
    assert selection.losers == ("jane",)
    assert selection.rule == RULE_MOST_COMPLETE_SOURCE
    assert selection.source_graph == RUN_B


def test_a_tie_is_broken_stably_not_by_iteration_order() -> None:
    """Two unifier runs over one store must produce the same graph.

    `graph:canonical` is dropped and rewritten on every pass, so a
    nondeterministic winner would make the store flap between two states with
    nothing to say which is current.
    """
    records = [
        Record(
            iri="https://github.com/acme/tool",
            entity_type="schema:SoftwareSourceCode",
            graph=graph,
            properties={"schema:name": [name]},
        )
        for graph, name in ((RUN_A, "zeta"), (RUN_B, "alpha"))
    ]
    forward = merge_cluster(
        cluster_records(records, RESOLVERS["schema:SoftwareSourceCode"])[0],
        default_policy(),
    )
    backward = merge_cluster(
        cluster_records(list(reversed(records)), RESOLVERS["schema:SoftwareSourceCode"])[0],
        default_policy(),
    )

    assert forward.properties["schema:name"] == backward.properties["schema:name"]
    selection = next(s for s in forward.selections if s.prop == "schema:name")
    assert selection.rule == RULE_FIRST_STABLE


def test_an_uncontested_value_is_recorded_but_not_contested() -> None:
    """Only real decisions need reifying — the "derived only" grain.

    A single-candidate value is already attributed by the named graph it came
    from, so reifying it would multiply storage for information the substrate
    holds.
    """
    cluster = cluster_records(
        [_person(ORCID_IRI, RUN_A, **{"schema:name": ["Jane Doe"]})],
        RESOLVERS["schema:Person"],
    )[0]
    merged = merge_cluster(cluster, default_policy())

    selection = next(s for s in merged.selections if s.prop == "schema:name")
    assert selection.rule == RULE_ONLY_CANDIDATE
    assert selection.contested is False
    assert contested_selections([merged]) == []


def test_the_run_anchor_never_reaches_the_canonical_node() -> None:
    """`pulse:partOfRun` is the substrate's provenance, not a canonical value.

    Nine of the twelve cross-run differences in the corpus are this property.
    Carrying a winner would assert that a canonical entity belongs to one
    arbitrary run's slice.
    """
    cluster = cluster_records(
        [
            _person(ORCID_IRI, RUN_A, **{"pulse:partOfRun": [{"@id": RUN_A}]}),
            _person(ORCID_IRI, RUN_B, **{"pulse:partOfRun": [{"@id": RUN_B}]}),
        ],
        RESOLVERS["schema:Person"],
    )[0]
    merged = merge_cluster(cluster, default_policy())

    assert "pulse:partOfRun" not in merged.properties


def test_a_capped_property_is_not_wrapped_in_a_list() -> None:
    """`sh:maxCount 1` means the generated model wants a scalar.

    `schema:author` on a Contribution is the case: capped there, unbounded on
    articles and repositories, so the same property serialises both ways
    depending on the subject's type.
    """
    cluster = cluster_records(
        [
            Record(
                iri="https://orcid.org/x__https://github.com/a/b",
                entity_type="pulse:Contribution",
                graph=RUN_A,
                properties={
                    "schema:author": [{"@id": ORCID_IRI}],
                    "pulse:contributionCount": [7],
                },
            ),
        ],
        RESOLVERS["org:Membership"],  # empty match keys: cluster by IRI
    )[0]
    cluster.entity_type = "pulse:Contribution"
    policy = default_policy()
    policy.resolvers["pulse:Contribution"] = RESOLVERS["org:Membership"]
    merged = merge_cluster(cluster, policy)

    assert merged.properties["schema:author"] == {"@id": ORCID_IRI}
    assert merged.properties["pulse:contributionCount"] == [7]


# --------------------------------------------------------------------------
# remapping
# --------------------------------------------------------------------------


def test_references_to_a_renamed_entity_are_rewritten() -> None:
    """A rename leaves every inbound edge dangling.

    `graph:canonical` is validated by closed shapes with `sh:class`
    constraints, and `prune_dangling_refs` is a *pipeline* stage that does not
    run here — so nothing downstream would catch it.
    """
    records = [
        _person(
            "https://github.com/jane",
            RUN_A,
            **{"pulse:hasProfile": [{"@id": PROFILE}]},
        ),
        _person(
            ORCID_IRI,
            RUN_B,
            **{
                "pulse:orcidIdentifier": [ORCID],
                "pulse:hasProfile": [{"@id": PROFILE}],
            },
        ),
        Record(
            iri="https://github.com/acme/tool",
            entity_type="schema:SoftwareSourceCode",
            graph=RUN_A,
            properties={"schema:author": [{"@id": "https://github.com/jane"}]},
        ),
    ]
    merged, report = unify_records(records)

    repo = _by_iri(merged)["https://github.com/acme/tool"]
    assert repo.properties["schema:author"] == [{"@id": ORCID_IRI}]
    assert report.remapped_references == 1


def test_a_renamed_endpoint_with_an_underscore_rebuilds_the_composite_id() -> None:
    """Renaming an endpoint changes a membership's *identity*, not a field.

    Rewriting the reference and leaving the id is how you get a membership
    whose id names one person and whose properties name another. The
    convention is `__`; `AGENTS.md` accepts a single `_` when parsing legacy
    graphs but splitting on it here would shred any IRI containing one — so
    the renamed endpoint carries one. With a plain `github.com/jane` a
    single-`_` split happens to rebuild the same string and proves nothing.
    """
    old_person = "https://github.com/jane_doe"
    profile = "urn:pulse:profile:github:jane_doe"
    org = "https://ror.org/02s376052"
    records = [
        _person(old_person, RUN_A, **{"pulse:hasProfile": [{"@id": profile}]}),
        _person(
            ORCID_IRI,
            RUN_B,
            **{"pulse:orcidIdentifier": [ORCID], "pulse:hasProfile": [{"@id": profile}]},
        ),
        Record(
            iri=f"{old_person}__{org}",
            entity_type="org:Membership",
            graph=RUN_A,
            properties={"org:organization": [{"@id": org}], "org:role": ["Researcher"]},
        ),
    ]
    merged, _report = unify_records(records)

    assert f"{ORCID_IRI}__{org}" in _by_iri(merged)
    assert f"{old_person}__{org}" not in _by_iri(merged)


def test_alias_map_skips_self_mappings() -> None:
    merged, _report = unify_records([_person(ORCID_IRI, RUN_A)])

    assert alias_map(merged) == {}


def test_external_references_are_reported_not_pruned() -> None:
    """Not every dangling reference is a break.

    The canonical graph legitimately points at nodes the unifier does not
    own — an SPDX licence, a Wikidata discipline. Measured over the real
    corpus, those are the *only* dangling references left once profiles are
    carried: 55 of them, all SPDX. Pruning them would delete real data.
    """
    merged, _report = unify_records(
        [
            Record(
                iri="https://github.com/acme/tool",
                entity_type="schema:SoftwareSourceCode",
                graph=RUN_A,
                properties={
                    "schema:license": [{"@id": "https://spdx.org/licenses/MIT.html"}],
                },
            ),
        ],
    )

    dangling = dangling_references(merged)
    assert dangling == {
        "https://github.com/acme/tool": ["https://spdx.org/licenses/MIT.html"],
    }
    # Still present: reporting is not pruning.
    assert merged[0].properties["schema:license"] == [
        {"@id": "https://spdx.org/licenses/MIT.html"},
    ]


def test_remap_is_a_no_op_without_renames() -> None:
    records = [_person(ORCID_IRI, RUN_A, **{"schema:name": ["Jane"]})]
    merged, _report = unify_records(records)
    before = dict(merged[0].properties)

    remap_entities(merged, {})

    assert merged[0].properties == before


# --------------------------------------------------------------------------
# the whole pass
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "profile"),
    [
        pytest.param(
            _person(ORCID_IRI, RUN_A, **{"pulse:hasProfile": [{"@id": PROFILE}]}),
            Record(
                iri=PROFILE,
                entity_type="pulse:PlatformProfile",
                graph=RUN_A,
                properties={
                    "pulse:platform": [{"@id": "pulse:GitHub"}],
                    "pulse:platformUsername": ["jane"],
                },
            ),
            id="person",
        ),
        pytest.param(
            Record(
                iri="https://ror.org/02s376052",
                entity_type="org:Organization",
                graph=RUN_A,
                properties={"pulse:hasOrganizationProfile": [{"@id": ORG_PROFILE}]},
            ),
            Record(
                iri=ORG_PROFILE,
                entity_type="pulse:OrganizationProfile",
                graph=RUN_A,
                properties={
                    "pulse:platform": [{"@id": "pulse:GitHub"}],
                    "pulse:organizationHandle": ["epfl"],
                },
            ),
            id="organization",
        ),
    ],
)
def test_profiles_are_carried_so_their_edges_resolve(subject: Record, profile: Record) -> None:
    """`PersonShape` constrains `pulse:hasProfile` with `sh:class`.

    So does `OrganizationShape`, for `pulse:hasOrganizationProfile`. A profile
    has nothing to unify — its IRI is minted from (platform, handle) — but
    skipping it leaves every subject's profile edge pointing at a node that is
    not in the graph.
    """
    merged, _report = unify_records([subject, profile])

    assert profile.iri in _by_iri(merged)
    # The subject's profile edge now resolves. What remains unresolved is the
    # profile's own `pulse:platform`, which points at an enumeration member —
    # vocabulary, not an entity, and the reason `dangling_references` is a
    # diagnostic rather than an invariant.
    assert subject.iri not in dangling_references(merged)


@pytest.mark.parametrize(
    ("github", "gitlab"),
    [
        pytest.param(
            Record(
                iri="urn:pulse:profile:github:jane",
                entity_type="pulse:PlatformProfile",
                graph=RUN_A,
                properties={
                    "pulse:platform": [{"@id": "pulse:GitHub"}],
                    "pulse:platformUsername": ["jane"],
                },
            ),
            Record(
                iri="urn:pulse:profile:gitlab:jane",
                entity_type="pulse:PlatformProfile",
                graph="urn:pulse:output:run-b:gitlab",
                properties={
                    "pulse:platform": [{"@id": "pulse:GitLab"}],
                    "pulse:platformUsername": ["jane"],
                },
            ),
            id="person",
        ),
        pytest.param(
            Record(
                iri=ORG_PROFILE,
                entity_type="pulse:OrganizationProfile",
                graph=RUN_A,
                properties={
                    "pulse:platform": [{"@id": "pulse:GitHub"}],
                    "pulse:organizationHandle": ["epfl"],
                },
            ),
            Record(
                iri="urn:pulse:org-profile:gitlab:epfl",
                entity_type="pulse:OrganizationProfile",
                graph="urn:pulse:output:run-b:gitlab",
                properties={
                    "pulse:platform": [{"@id": "pulse:GitLab"}],
                    "pulse:organizationHandle": ["epfl"],
                },
            ),
            id="organization",
        ),
    ],
)
def test_one_handle_on_two_platforms_is_two_profiles(github: Record, gitlab: Record) -> None:
    """A profile clusters by IRI alone: none of its properties is a match key.

    The IRI is minted from (platform, handle), so one handle on two platforms
    is two accounts, and nothing says they share an owner. Matching on the
    handle would fuse them into one profile whose `pulse:platform` — capped at
    one — then has to pick which of the two platforms the account is on.
    """
    merged, _report = unify_records([github, gitlab])

    assert sorted(entity.iri for entity in merged) == sorted([github.iri, gitlab.iri])


def test_a_type_with_no_resolver_is_skipped_not_dropped_silently() -> None:
    """`pulse:ExtractionOutput` belongs to the substrate, not to canonical.

    Skipping is right; doing it without a trace is not, so the runner logs it.
    Asserted through the report rather than the log: a type with no resolver
    contributes no cluster and no entity.
    """
    merged, report = unify_records(
        [
            Record(
                iri="urn:pulse:output:run-a:github",
                entity_type="pulse:ExtractionOutput",
                graph=RUN_A,
                properties={"pulse:platform": [{"@id": "pulse:GitHub"}]},
            ),
        ],
    )

    assert merged == []
    assert report.clusters == 0
    assert report.records_read == 1


def test_the_report_counts_cross_run_clusters() -> None:
    """The number this phase exists to move off zero."""
    _merged, report = unify_records(
        [
            _person(ORCID_IRI, RUN_A, **{"schema:name": ["Jane"]}),
            _person(ORCID_IRI, RUN_B, **{"schema:name": ["Jane"]}),
            _person("https://github.com/solo", RUN_A, **{"schema:name": ["Solo"]}),
        ],
    )

    assert report.clusters == EXPECTED_TWO
    assert report.cross_graph_clusters == 1
    assert report.graphs_read == EXPECTED_TWO


def test_the_canonical_document_is_one_named_graph() -> None:
    """Same nested-`@graph` form the substrate uses, so it loads the same way."""
    merged, _report = unify_records([_person(ORCID_IRI, RUN_A, **{"schema:name": ["Jane"]})])
    document = canonical_document(merged, context={"pulse": "x"})

    assert [entry["@id"] for entry in document["@graph"]] == [CANONICAL_GRAPH]
    assert document["@graph"][0]["@graph"][0]["@id"] == ORCID_IRI


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"@id": "https://x"}, ["https://x"]),
        ([{"@id": "https://x"}, {"@id": "https://y"}], ["https://x", "https://y"]),
        ("plain", ["plain"]),
        (["a", "b"], ["a", "b"]),
        (None, []),
        ([{"no-id": 1}], []),
        (7, [7]),
    ],
)
def test_record_values_flattens_references_and_literals(raw: Any, expected: list[Any]) -> None:
    """The merger reasons over flat values; the shape of the input varies.

    A reference has to come back as its IRI string so match keys can compare
    it, and be *rebuilt* as `{"@id": ...}` on the way out — serialising an edge
    as a literal is the defect the reader's `sh:or` bug caused in §3c.
    """
    record = Record(iri="x", entity_type="schema:Person", graph=RUN_A, properties={"p": raw})

    assert record.values("p") == expected


def test_the_remap_leaves_no_stale_reference() -> None:
    """The invariant `dangling_references` cannot express.

    Every IRI in the alias map was an entity in this batch and is now
    something else, so a surviving reference to one is broken by construction —
    no false positives, unlike a licence URL or an enumeration member.
    """
    records = [
        _person("https://github.com/jane", RUN_A, **{"pulse:hasProfile": [{"@id": PROFILE}]}),
        _person(
            ORCID_IRI,
            RUN_B,
            **{"pulse:orcidIdentifier": [ORCID], "pulse:hasProfile": [{"@id": PROFILE}]},
        ),
        Record(
            iri="https://github.com/acme/tool",
            entity_type="schema:SoftwareSourceCode",
            graph=RUN_A,
            properties={"schema:author": [{"@id": "https://github.com/jane"}]},
        ),
    ]
    merged, report = unify_records(records)

    assert report.stale == {}
    assert stale_references(merged, alias_map(merged)) == {}


def test_a_missed_remap_is_caught_by_the_invariant() -> None:
    """Mutation check: skip the remap and the invariant has to notice.

    Without this, `report.stale == {}` proves only that nothing was renamed.
    """
    entity_iri = "https://github.com/acme/tool"
    merged, _report = unify_records(
        [
            Record(
                iri=entity_iri,
                entity_type="schema:SoftwareSourceCode",
                graph=RUN_A,
                properties={"schema:author": [{"@id": "https://github.com/jane"}]},
            ),
        ],
    )

    # A rename the remap never saw.
    pretend = {"https://github.com/jane": ORCID_IRI}

    assert stale_references(merged, pretend) == {
        entity_iri: ["https://github.com/jane"],
    }


# --------------------------------------------------------------------------
# aggregation - counts and date ranges
# --------------------------------------------------------------------------


def _contribution(graph: str, **props: Any) -> Record:
    return Record(
        iri="https://github.com/jane__https://github.com/acme/tool",
        entity_type="pulse:Contribution",
        graph=graph,
        properties={
            "pulse:contributionTo": [{"@id": "https://github.com/acme/tool"}],
            "schema:author": [{"@id": "https://github.com/jane"}],
            **props,
        },
    )


def test_a_re_observed_count_takes_the_maximum_not_the_sum() -> None:
    """The intuitive choice is a sum, and it is wrong.

    `pulse:contributionCount` is GitHub's *running total* for a person on a
    repository, so two runs a week apart report 40 and then 43 — not 40 and 3
    more. Summing them double-counts every commit and produces 83, a number
    with no referent.

    Summing across genuinely distinct platforms would be defensible, but this
    layer merges a cluster without knowing which slice each value came from.
    `MAX` under-counts a multi-platform contributor and never over-counts
    anyone, which is the safer error for a figure people read as effort.
    """
    merged, _report = unify_records(
        [
            _contribution(RUN_A, **{"pulse:contributionCount": [40]}),
            _contribution(RUN_B, **{"pulse:contributionCount": [43]}),
        ],
    )

    # A list, because `ContributionShape` puts no `sh:maxCount` on the count
    # — the same loose cardinality the generated models expose everywhere.
    assert merged[0].properties["pulse:contributionCount"] == [43]


def test_a_date_range_widens_in_both_directions() -> None:
    """First is the earliest ever seen; last is the latest.

    Neither is a choice between sources — the answer is a property of the
    whole candidate set, which is why these are `MIN`/`MAX` rather than
    `SELECT`.
    """
    merged, _report = unify_records(
        [
            _contribution(
                RUN_A,
                **{
                    "pulse:firstContributionDate": ["2019-03-01T00:00:00"],
                    "pulse:lastContributionDate": ["2021-06-01T00:00:00"],
                },
            ),
            _contribution(
                RUN_B,
                **{
                    "pulse:firstContributionDate": ["2018-01-01T00:00:00"],
                    "pulse:lastContributionDate": ["2024-11-01T00:00:00"],
                },
            ),
        ],
    )
    contribution = merged[0].properties

    assert contribution["pulse:firstContributionDate"] == ["2018-01-01T00:00:00"]
    assert contribution["pulse:lastContributionDate"] == ["2024-11-01T00:00:00"]


def test_an_aggregated_value_records_that_it_was_aggregated() -> None:
    """Not `most-complete-source`: nothing was chosen.

    The provenance record has to say which rule produced the value, and
    "the maximum of what several sources said" is a different claim from
    "the value the most complete source gave".
    """
    from git_metadata_extractor.unify.merge import RULE_AGGREGATED

    merged, report = unify_records(
        [
            _contribution(RUN_A, **{"pulse:contributionCount": [40]}),
            _contribution(RUN_B, **{"pulse:contributionCount": [43]}),
        ],
    )
    selection = next(
        s for s in merged[0].selections if s.prop == "pulse:contributionCount"
    )

    assert selection.rule == f"{RULE_AGGREGATED}:max"
    assert selection.winner == 43  # noqa: PLR2004
    assert selection.losers == (40,)
    assert selection.source_graph == RUN_B
    assert len(report.contested) == 1


def test_mixed_value_types_are_not_ordered_silently() -> None:
    """A count that arrived as a string from one source cannot be compared.

    Coercing here would silently order by whatever `max` does with mixed
    types — for two strings that is lexical, so "9" would beat "40". Falling
    back leaves the value out rather than inventing a wrong one.
    """
    merged, _report = unify_records(
        [
            _contribution(RUN_A, **{"pulse:contributionCount": [40]}),
            _contribution(RUN_B, **{"pulse:contributionCount": ["43"]}),
        ],
    )

    assert "pulse:contributionCount" not in merged[0].properties


def test_a_single_observation_is_not_reported_as_contested() -> None:
    """One candidate is not an aggregation anyone needs explained."""
    merged, report = unify_records(
        [_contribution(RUN_A, **{"pulse:contributionCount": [40]})],
    )

    assert merged[0].properties["pulse:contributionCount"] == [40]
    assert report.contested == []


def test_two_runs_of_one_contribution_cluster_by_their_composite_id() -> None:
    """A composite `{person}__{repo}` already identifies the pair.

    So clustering by IRI is exactly right and no match key is needed — and
    `remap` rebuilds the id if either endpoint is renamed.
    """
    _merged, report = unify_records(
        [
            _contribution(RUN_A, **{"pulse:contributionCount": [40]}),
            _contribution(RUN_B, **{"pulse:contributionCount": [43]}),
        ],
    )

    assert report.by_type["pulse:Contribution"] == 1
    assert report.cross_graph_clusters == 1
