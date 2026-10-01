"""Tests for the v2 -> v3 **raw** (substrate) projection.

The layer `canonical_projection` was skipping. Canonical is `sh:closed`, so
translating flat-v2 straight into it dropped everything canonical has no slot
for — follower counts, biographies, branch counts, ~20 repository fields the
providers already fetch. The raw shapes declare all of them, and the raw layer
is what a source actually said before anything was chosen between sources.

`raw_projection`'s module docstring has claimed since it was written that
`test_every_mapping_target_is_declared_by_its_shape` checks its mapping tables
against the real TTL "rather than trusting this docstring". **The test did not
exist.** So the tables had no coverage at all, while three of the five raw
shapes they target are `sh:closed` and reject any property they do not
declare — exactly the failure mode the citation promised was covered. It exists
now, and it is the first test in this file.

What the rest covers is the anchoring: which `pulse:ExtractionOutput` an entity
is attributed to. Every defect found there so far was invisible to SHACL — the
raw entity shapes for Person and Organization are open and require nothing, so
an entity with no anchor at all conforms perfectly while being unattributable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from git_metadata_extractor.pipeline.stages import raw_projection
from git_metadata_extractor.pipeline.stages.raw_projection import (
    _PART_OF_RUN_TYPES,
    _PASSTHROUGH,
    _platform_of,
    extraction_output_iri,
    project_raw,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = REPO_ROOT / "vendor" / "open-pulse-ontology" / "src" / "ontology"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "v2"))

STARS = 1497
FORKS = 408
FOLLOWERS = 147


def _by_id(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["@id"]: node for node in doc["@graph"]}


def _by_type(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for node in doc["@graph"]:
        out.setdefault(str(node.get("@type")), []).append(node)
    return out


# --------------------------------------------------------------------------
# the mapping tables, against the real shapes
# --------------------------------------------------------------------------

#: Each mapping table in `raw_projection`, and the shape whose properties it is
#: allowed to name. Three of these five are `sh:closed`.
_TABLE_SHAPES = [
    ("_PROFILE_FIELDS", "RawPlatformProfileShape"),
    ("_ORGANIZATION_PROFILE_FIELDS", "RawOrganizationProfileShape"),
    ("_REPOSITORY_FIELDS", "RawRepositoryShape"),
]

_PASSTHROUGH_SHAPES = {
    "schema:Person": "RawPersonShape",
    "org:Organization": "RawOrganizationShape",
    "schema:SoftwareSourceCode": "RawRepositoryShape",
    "schema:ScholarlyArticle": "RawArticleShape",
    "org:Membership": "RawMembershipShape",
}

pytest_ontology = pytest.mark.skipif(
    not ONTOLOGY.is_dir(),
    reason="ontology submodule not checked out (git submodule update --init)",
)


def _declared_properties(shape_name: str) -> set[str]:
    from ontology_reader import read_shapes  # noqa: PLC0415

    shapes = {
        shape.local_name: shape
        for shape in read_shapes(ONTOLOGY / "ontology-shapes-raw.ttl")
    }
    shape = shapes[shape_name]
    return {prop.path for prop in shape.properties} | set(shape.ignored_properties)


@pytest_ontology
@pytest.mark.parametrize(("table_name", "shape_name"), _TABLE_SHAPES)
def test_every_mapping_target_is_declared_by_its_shape(
    table_name: str,
    shape_name: str,
) -> None:
    """A mapping target the shape does not declare is a closed-shape violation.

    This is the test `raw_projection`'s docstring cites. It reads the pinned
    TTL rather than a copy of it, so a submodule bump that renames or drops a
    property fails here instead of surfacing as a SHACL warning on a live run.
    """
    table = getattr(raw_projection, table_name)
    declared = _declared_properties(shape_name)

    undeclared = sorted(set(table.values()) - declared)
    assert not undeclared, f"{shape_name} does not declare {undeclared}"


@pytest_ontology
@pytest.mark.parametrize(
    ("node_type", "shape_name"),
    sorted(_PASSTHROUGH_SHAPES.items()),
)
def test_every_passthrough_property_is_declared_by_its_shape(
    node_type: str,
    shape_name: str,
) -> None:
    """Same contract for the properties carried straight through from v2."""
    declared = _declared_properties(shape_name)
    undeclared = sorted(set(_PASSTHROUGH.get(node_type, ())) - declared)
    assert not undeclared, f"{shape_name} does not declare {undeclared}"


@pytest_ontology
def test_part_of_run_is_stamped_only_where_the_shape_declares_it() -> None:
    """`RawMembershipShape` is closed and has no anchor slot.

    Stamping `pulse:partOfRun` on everything made every membership violate.
    The set in the module has to match the shapes, not a guess about which
    entities "belong to a run".
    """
    from ontology_reader import read_shapes  # noqa: PLC0415

    shapes = read_shapes(ONTOLOGY / "ontology-shapes-raw.ttl")
    declares_anchor = {
        shape.target_class
        for shape in shapes
        if any(prop.path == "pulse:partOfRun" for prop in shape.properties)
    }

    assert declares_anchor >= _PART_OF_RUN_TYPES
    # Memberships are the case that motivated the distinction: a raw fact
    # (ORCID asserts employment) whose shape still has no anchor slot.
    assert "org:Membership" not in _PART_OF_RUN_TYPES


# --------------------------------------------------------------------------
# anchoring — which extraction output an entity is attributed to
# --------------------------------------------------------------------------


def test_github_entities_anchor_to_the_github_output() -> None:
    doc = project_raw(
        [
            {
                "@id": "https://github.com/ANTsX/ANTs",
                "@type": "schema:SoftwareSourceCode",
                "schema:name": "ANTs",
                "pulse:githubRepositoryHandle": "https://github.com/ANTsX/ANTs",
                "pulse:githubRepoStars": STARS,
                "pulse:githubRepoForks": FORKS,
            },
        ],
        run_id="r1",
    )
    repo = _by_id(doc)["https://github.com/ANTsX/ANTs"]

    assert repo["pulse:partOfRun"] == {"@id": extraction_output_iri("r1", "pulse:GitHub")}
    assert repo["pulse:repositoryStars"] == STARS
    assert repo["pulse:repositoryForks"] == FORKS


def test_ror_organizations_anchor_to_a_ror_output() -> None:
    """A registry lookup is a source, and has to name itself as one.

    Without `pulse:ROR` in `PlatformEnumeration` (ontology patch 05) a
    ROR-identified organization has no platform, so no
    `pulse:ExtractionOutput`, so no anchor — and it fell through into the graph
    that describes the extraction, as though the registry's name for it were a
    fact about the run rather than a fact the run found.
    """
    doc = project_raw(
        [
            {
                "@id": "https://ror.org/03zh00e46",
                "@type": "org:Organization",
                "schema:name": "Normalization Housing Foundation",
            },
        ],
        run_id="r1",
    )
    org = _by_id(doc)["https://ror.org/03zh00e46"]
    output = _by_id(doc)[extraction_output_iri("r1", "pulse:ROR")]

    assert org["pulse:partOfRun"] == {"@id": output["@id"]}
    assert output["pulse:platform"] == "pulse:ROR"


def test_a_ror_organization_gets_no_profile() -> None:
    """A ROR id is an identity, not an account.

    `ror.org` is deliberately absent from `canonical_projection`'s host table:
    adding it there to get the anchor would mint an `OrganizationProfile` with
    no profile page, no handle and no follower count behind it.
    """
    doc = project_raw(
        [
            {
                "@id": "https://ror.org/03zh00e46",
                "@type": "org:Organization",
                "schema:name": "Normalization Housing Foundation",
            },
        ],
        run_id="r1",
    )

    assert "pulse:OrganizationProfile" not in _by_type(doc)


def test_articles_anchor_to_the_repository_that_holds_the_record() -> None:
    """An article's id is its DOI, and `doi.org` is a resolver, not a source.

    So the projected node offers no platform signal at all and every article
    was stranded. The Infoscience URL it was found through is the signal, and
    it only exists on the flat node — which is why `_platform_of` reads it.
    """
    doc = project_raw(
        [
            {
                "@id": "https://doi.org/10.1093/mnras/stad3265",
                "@type": "schema:ScholarlyArticle",
                "schema:name": "Probing the roles of orientation",
                "schema:identifier": "https://doi.org/10.1093/mnras/stad3265",
                "pulse:infoscienceArticleIdentifier": (
                    "https://infoscience.epfl.ch/entities/publication/78b678d1"
                ),
            },
        ],
        run_id="r1",
    )
    article = _by_id(doc)["https://doi.org/10.1093/mnras/stad3265"]

    assert article["pulse:partOfRun"] == {
        "@id": extraction_output_iri("r1", "pulse:Infoscience"),
    }
    assert article["pulse:doi"] == ["10.1093/mnras/stad3265"]


def test_a_platform_profile_beats_a_registry_id_for_the_anchor() -> None:
    """The anchor records what asserted the properties, not what named the node.

    An organization discovered through its GitHub org and *then* resolved to a
    ROR keeps the GitHub anchor. Per-value attribution is finer than one anchor
    per entity can express; that is the provenance writer's job.
    """
    platform = _platform_of(
        {"@id": "https://ror.org/03zh00e46"},
        [{"@id": "urn:pulse:org-profile:github:ANTsX", "pulse:platform": "pulse:GitHub"}],
        {},
    )

    assert platform == "pulse:GitHub"


def test_nothing_is_excluded_from_the_substrate_today() -> None:
    """`_DERIVED_TYPES` is empty, and the distinction it encodes is still real.

    `pulse:Contribution` was its only member, excluded as a "derived edge
    because we compute commit counts" — see
    `test_a_contribution_is_carried_by_the_substrate` for why that premise was
    wrong and what it cost.

    The set stays because the boundary is genuine: a type the *unifier*
    computes rather than a source reports does not belong in the layer that
    records what sources said. Nothing meets that description right now.
    """
    from git_metadata_extractor.pipeline.stages.raw_projection import _DERIVED_TYPES

    assert _DERIVED_TYPES == frozenset()

    doc = project_raw(
        [
            {
                "@id": "urn:pulse:person:x__https://github.com/a/b",
                "@type": "pulse:Contribution",
                "pulse:contributionCount": 1,
            },
        ],
        run_id="r1",
    )

    assert len(doc["@graph"]) == 1


def test_memberships_keep_their_properties() -> None:
    """`RawMembershipShape` requires nothing, so an empty one validates.

    Which is how the substrate came to carry `<id> a org:Membership .` and
    nothing else: `_PASSTHROUGH` had no entry for the type, and no shape
    complained.
    """
    doc = project_raw(
        [
            {
                "@id": "https://orcid.org/0000-0002-1825-0097__https://ror.org/02s376052",
                "@type": "org:Membership",
                "org:organization": {"@id": "https://ror.org/02s376052"},
                "org:role": "Postdoctoral Researcher",
                "time:hasBeginning": "2019-01-01",
                "time:hasEnd": "2021-12-31",
            },
        ],
        run_id="r1",
    )
    membership = doc["@graph"][0]

    assert membership["org:role"] == "Postdoctoral Researcher"
    assert membership["org:organization"] == {"@id": "https://ror.org/02s376052"}
    assert membership["time:hasBeginning"] == "2019-01-01"
    # No anchor: the shape is closed and declares none. The named graph it is
    # written into is its only attribution.
    assert "pulse:partOfRun" not in membership


def test_profile_detail_lands_on_the_profile_not_the_person() -> None:
    """A follower count is a fact about an account, not about a person."""
    doc = project_raw(
        [
            {
                "@id": "https://github.com/octocat",
                "@type": "schema:Person",
                "schema:name": "The Octocat",
                "pulse:githubUsername": "https://github.com/octocat",
                "_followers_count": FOLLOWERS,
                "_bio": "A cat that codes",
                "_location": "San Francisco",
            },
        ],
        run_id="r1",
    )
    by_type = _by_type(doc)
    person = by_type["schema:Person"][0]
    profile = by_type["pulse:PlatformProfile"][0]

    assert profile["pulse:followerCount"] == FOLLOWERS
    assert profile["pulse:biography"] == "A cat that codes"
    assert profile["pulse:location"] == "San Francisco"
    assert "pulse:followerCount" not in person
    assert person["pulse:hasProfile"] == [{"@id": profile["@id"]}]


# --------------------------------------------------------------------------
# sufficiency: what the canonical layer needs the substrate to hold
# --------------------------------------------------------------------------
#
# These are not raw-shape conformance. Raw conformance was 119/119 *without*
# any of them, because `RawArticleShape` puts no `sh:minCount` on
# `pulse:hasDeposit` and `RawOrganizationShape` is open. They were found by
# unifying the substrate into `graph:canonical` and validating *that* — 66
# violations, all from the substrate lacking something canonical requires.
#
# The substrate is the durable layer. Anything the canonical layer needs and
# the substrate does not hold is unrecoverable from the store, however
# well-formed the raw graph is. Well-formed and sufficient are different
# questions, and only the second one matters for a layer nothing rebuilds.


def test_a_ror_identified_organization_carries_its_ror() -> None:
    """`build_jsonld_output` strips `pulse:ror` when the `@id` *is* the ROR.

    So the flat form the substrate projects from has no `pulse:ror` at all.
    The canonical projection re-derives it (§3f, the 50% -> 85% fix); the raw
    projection did not, with two consequences that only surfaced downstream:
    40 of 102 organizations failed `OrganizationShape`'s identity `sh:or`, and
    the unifier's *primary* organization match key never fired once.
    """
    doc = project_raw(
        [
            {
                "@id": "https://ror.org/02s376052",
                "@type": "org:Organization",
                "schema:name": "EPFL",
            },
        ],
        run_id="r1",
    )
    org = _by_id(doc)["https://ror.org/02s376052"]

    # URL-shaped, not bare: `pulse:ror` is v3's deliberate exception (§2.4) and
    # `RawOrganizationShape` patterns the URL form.
    assert org["pulse:ror"] == "https://ror.org/02s376052"


def test_a_github_identified_organization_gets_no_ror() -> None:
    """Only derive it from an id that actually is one."""
    doc = project_raw(
        [
            {
                "@id": "https://github.com/ANTsX",
                "@type": "org:Organization",
                "schema:name": "ANTsX",
            },
        ],
        run_id="r1",
    )

    assert "pulse:ror" not in _by_id(doc)["https://github.com/ANTsX"]


def test_an_article_carries_the_deposit_its_date_lives_on() -> None:
    """`ArticleShape` requires `pulse:hasDeposit`; `ArticleShape` has no date.

    v3 puts `schema:datePublished` on the deposit — the abstract work has no
    single publication date, its platform records do. So an article without a
    deposit is both non-conformant *and* dateless, and 26 of 26 corpus
    articles were exactly that until the deposit moved into this projection.
    """
    doc = project_raw(
        [
            {
                "@id": "https://doi.org/10.1093/mnras/stad3265",
                "@type": "schema:ScholarlyArticle",
                "schema:name": "Probing the roles of orientation",
                "schema:datePublished": "2023-12-23",
                "pulse:infoscienceArticleIdentifier": (
                    "https://infoscience.epfl.ch/entities/publication/78b678d1"
                ),
            },
        ],
        run_id="r1",
    )
    by_type = _by_type(doc)
    article = by_type["schema:ScholarlyArticle"][0]
    deposit = by_type["pulse:Deposit"][0]

    assert article["pulse:hasDeposit"] == [{"@id": deposit["@id"]}]
    assert deposit["schema:datePublished"] == "2023-12-23"
    assert deposit["pulse:platform"] == "pulse:Infoscience"
    assert deposit["pulse:depositOf"] == {"@id": article["@id"]}


def test_no_partial_deposit_is_emitted() -> None:
    """`DepositShape` requires both a platform and a date.

    A deposit missing either would be a node in the durable layer that can
    never be promoted — worse than its absence, because absence is visible.
    """
    doc = project_raw(
        [
            {
                "@id": "https://doi.org/10.1/x",
                "@type": "schema:ScholarlyArticle",
                "schema:name": "No date",
                "pulse:infoscienceArticleIdentifier": (
                    "https://infoscience.epfl.ch/entities/publication/abc"
                ),
            },
            {
                "@id": "https://doi.org/10.1/y",
                "@type": "schema:ScholarlyArticle",
                "schema:name": "No platform record",
                "schema:datePublished": "2024-01-01",
            },
        ],
        run_id="r1",
    )

    assert "pulse:Deposit" not in _by_type(doc)
    for article in _by_type(doc)["schema:ScholarlyArticle"]:
        assert "pulse:hasDeposit" not in article


# --------------------------------------------------------------------------
# contributions: a raw fact after all
# --------------------------------------------------------------------------


def test_a_contribution_is_carried_by_the_substrate() -> None:
    """`_DERIVED_TYPES` excluded these, on a premise that was wrong.

    The premise was that "we compute commit counts". We do not:
    `agents/rule_based/contribution_agent.py` reads GitHub's `contributions`
    field, so the number is something a platform *reports* and belongs in the
    layer that records what sources said.

    Two facts settled it. `RawPersonShape` already declares
    `pulse:hasContribution` with `sh:class pulse:Contribution`, so the raw
    layer expects these nodes — while no raw shape targeted the class, making
    it the one entity type the raw layer referenced and could not validate.
    And the canonical `ContributionShape` requires `pulse:contributionCount`
    (`sh:minCount 1`), so with the count absent from the substrate the unifier
    could not assert an edge at all: the store-side canonical graph had zero
    contributions while `/v2/extract` returned 46.
    """
    doc = project_raw(
        [
            {
                "@id": "https://github.com/jane__https://github.com/acme/tool",
                "@type": "pulse:Contribution",
                "pulse:contributionTo": {"@id": "https://github.com/acme/tool"},
                "schema:author": {"@id": "https://github.com/jane"},
                "pulse:contributionCount": 12,
            },
        ],
        run_id="r1",
    )
    contribution = _by_type(doc)["pulse:Contribution"][0]

    assert contribution["pulse:contributionCount"] == 12  # noqa: PLR2004
    assert contribution["pulse:contributionTo"] == {"@id": "https://github.com/acme/tool"}
    assert contribution["schema:author"] == {"@id": "https://github.com/jane"}


def test_a_contribution_carries_no_run_anchor() -> None:
    """`RawContributionShape` declares no `pulse:partOfRun`, like Membership.

    The anchor belongs to entities a source describes, not to the edges
    between them — so the named graph it is written into is its only
    attribution, which is what `substrate._NAMES_SUBJECT` has to route.
    """
    doc = project_raw(
        [
            {
                "@id": "https://github.com/jane__https://github.com/acme/tool",
                "@type": "pulse:Contribution",
                "pulse:contributionTo": {"@id": "https://github.com/acme/tool"},
                "pulse:contributionCount": 1,
            },
        ],
        run_id="r1",
    )

    assert "pulse:partOfRun" not in _by_type(doc)["pulse:Contribution"][0]
    assert "pulse:Contribution" not in _PART_OF_RUN_TYPES


@pytest_ontology
def test_the_contribution_passthrough_matches_the_raw_shape() -> None:
    """The shape is `sh:closed`, so an extra property is a violation.

    Same guard as the other passthrough tables, listed separately because
    `RawContributionShape` arrived with ontology patch 06 (upstreamed in
    open-pulse-ontology#27) — if an ontology bump drops it, this fails loudly.
    """
    declared = _declared_properties("RawContributionShape")
    undeclared = sorted(set(_PASSTHROUGH["pulse:Contribution"]) - declared)

    assert not undeclared, f"RawContributionShape does not declare {undeclared}"


@pytest_ontology
def test_the_raw_contribution_shape_requires_nothing() -> None:
    """The canonical one requires three properties; the raw one must not.

    The substrate is append-only and the only durable copy of what a run
    found, so it must never refuse a slice for an incomplete assertion. The
    requirement bites at the canonical gate instead — which is the whole
    substrate-open / canonical-closed split.
    """
    from ontology_reader import read_shapes  # noqa: PLC0415

    raw = next(
        shape
        for shape in read_shapes(ONTOLOGY / "ontology-shapes-raw.ttl")
        if shape.local_name == "RawContributionShape"
    )
    canonical = next(
        shape
        for shape in read_shapes(ONTOLOGY / "ontology-shapes-canonical.ttl")
        if shape.local_name == "ContributionShape"
    )

    assert not [prop.path for prop in raw.properties if prop.required]
    assert sorted(prop.path for prop in canonical.properties if prop.required) == [
        "pulse:contributionCount",
        "pulse:contributionTo",
        "schema:author",
    ]


# --------------------------------------------------------------------------
# source snapshots — which build of which index answered
# --------------------------------------------------------------------------
#
# `ExtractionOutputShape` is `sh:closed` over three properties, so before
# ontology patch 08 an output could name its platform but never the version of
# that platform's data it saw. `prov:used` -> `pulse:SourceSnapshot` is that
# slot, and these pin what does and does not get one.


def _outputs(document: dict) -> dict[str, dict]:
    return {
        str(n["pulse:platform"]): n
        for n in document["@graph"]
        if n.get("@type") == "pulse:ExtractionOutput"
    }


def _snapshots(document: dict) -> dict[str, dict]:
    return {
        str(n["@id"]): n
        for n in document["@graph"]
        if n.get("@type") == "pulse:SourceSnapshot"
    }


def test_an_index_backed_output_names_the_build_it_read() -> None:
    from git_metadata_extractor.pipeline.stages.raw_projection import (  # noqa: PLC0415
        index_version,
        source_snapshot_iri,
    )

    version = index_version()
    if not version:
        pytest.skip("open-pulse-sources not installed")

    document = project_raw(
        [
            {
                "@id": "https://ror.org/02s376052",
                "@type": "org:Organization",
                "schema:name": "EPFL",
            },
        ],
        run_id="snap-1",
    )

    expected = source_snapshot_iri("pulse:ROR", version)
    assert _outputs(document)["pulse:ROR"]["prov:used"] == {"@id": expected}

    snapshot = _snapshots(document)[expected]
    assert snapshot["schema:softwareVersion"] == version
    assert snapshot["pulse:platform"] == "pulse:ROR"
    assert snapshot["schema:name"] == "ror"


def test_github_gets_no_snapshot_because_its_data_is_live() -> None:
    """A version would claim a reproducibility the live REST API cannot give.

    The repository that answered yesterday can answer differently today, and no
    library version records that — so the honest output is one with no
    `prov:used` rather than one pointing at a build that did not produce it.
    """
    document = project_raw(
        [
            {
                "@id": "https://github.com/octocat/Hello-World",
                "@type": "schema:SoftwareSourceCode",
                "pulse:githubRepositoryHandle": "https://github.com/octocat/Hello-World",
            },
        ],
        run_id="snap-2",
    )

    assert "prov:used" not in _outputs(document)["pulse:GitHub"]
    assert _snapshots(document) == {}


def test_the_snapshot_iri_is_shared_across_runs_on_one_build() -> None:
    """"What the source was" is not per-run; "what a run made of it" is.

    Two runs against the same index build name the same snapshot, which is what
    lets a query ask "everything read from this version" across runs.
    """
    from git_metadata_extractor.pipeline.stages.raw_projection import (  # noqa: PLC0415
        index_version,
    )

    if not index_version():
        pytest.skip("open-pulse-sources not installed")

    node = {
        "@id": "https://ror.org/02s376052",
        "@type": "org:Organization",
        "schema:name": "EPFL",
    }
    first = _snapshots(project_raw([node], run_id="run-a"))
    second = _snapshots(project_raw([node], run_id="run-b"))

    assert first.keys() == second.keys()
    assert "run-a" not in next(iter(first))


def test_every_index_backed_platform_is_a_declared_enumeration_member() -> None:
    """A platform the ontology does not declare cannot name an output.

    `ExtractionOutputShape` and `SourceSnapshotShape` both constrain
    `pulse:platform` with `sh:class pulse:PlatformEnumeration`, so a member
    missing from the TTL is a violation on every slice it anchors. Patch 07
    added the five this service reads and that the enumeration lacked.
    """
    from git_metadata_extractor.pipeline.stages.raw_projection import (  # noqa: PLC0415
        _INDEX_BACKED,
    )
    from git_metadata_extractor.schema.generated.enumerations import (  # noqa: PLC0415
        PLATFORM_MEMBERS,
    )

    assert _INDEX_BACKED <= PLATFORM_MEMBERS
