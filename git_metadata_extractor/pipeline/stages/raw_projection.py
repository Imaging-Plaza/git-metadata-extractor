"""Project the extracted entities into the v3 **raw** (substrate) shapes.

The middle layer the pipeline was skipping. `canonical_projection` translated
the flat v2 form straight into the closed canonical shapes, which meant
everything the canonical layer has no slot for was simply dropped — follower
counts, biographies, locations, avatars, branch counts, release counts, ~20
repository fields the providers already fetch.

None of that was ever missing from the ontology. `RawPlatformProfileShape`
declares `pulse:followerCount`, `pulse:biography`, `pulse:location`,
`pulse:company`, `schema:image`, `schema:url`; `RawRepositoryShape` declares
`pulse:archived`, `pulse:defaultBranch`, `pulse:openIssueCount`,
`pulse:releaseCount`, `pulse:visibility` and forty more. The raw layer is
deliberately **open** at the entity level (`sh:closed false`) precisely so it
can hold what a source actually said, before the unifier decides what is
canonical.

**Provenance anchoring comes from the shapes, not from a convention we
invented.** `pulse:partOfRun` points at a `pulse:ExtractionOutput`, and
`ExtractionOutputShape` carries `prov:wasGeneratedBy` → `pulse:ExtractionRun`
plus `pulse:platform`. So the unit of substrate is *one platform's slice of one
run*, and that is what a named graph should be named after — which answers the
architecture document's open gap 3.

Note which raw shapes are closed: the entity shapes for Person and
Organization are open, but `RawRepositoryShape`, `RawArticleShape` and both
profile shapes are **closed**. So the mapping tables below may only name
properties those shapes declare;
`tests/v2/test_raw_projection.py::test_every_mapping_target_is_declared_by_its_shape`
checks that against the real TTL rather than trusting this docstring.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.pipeline.stages.canonical_projection import (
    _host_and_path,
    _is_empty,
    _platform_for,
    bare_doi,
    bare_handle,
    bare_orcid,
    deposit_iri,
    profile_iri,
)
from git_metadata_extractor.pipeline.stages.source_attribution import split_by_source

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)


def extraction_output_iri(run_id: str, platform: str) -> str:
    """One platform's slice of one run — the substrate's unit of grouping.

    This is the IRI a named graph should carry: `pulse:ExtractionOutput` is what
    `pulse:partOfRun` points at, and it links onward to the `ExtractionRun` via
    `prov:wasGeneratedBy`.
    """
    slug = platform.removeprefix("pulse:").lower()
    return f"urn:pulse:output:{run_id}:{slug}"


def extraction_output(
    *,
    run_id: str,
    platform: str,
    run_iri: str,
    generated_at: str | None = None,
    snapshot_iri: str | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "@id": extraction_output_iri(run_id, platform),
        "@type": "pulse:ExtractionOutput",
        "pulse:platform": platform,
        "prov:wasGeneratedBy": {"@id": run_iri},
    }
    if generated_at:
        node["prov:generatedAtTime"] = generated_at
    if snapshot_iri:
        node["prov:used"] = {"@id": snapshot_iri}
    return node


#: Platforms whose substrate content this service reads out of an
#: `open_pulse_sources` index, so the library build that produced that index is
#: what a result would have to be reproduced against.
#:
#: **GitHub is deliberately absent.** Its entity data comes from the live REST
#: API on every current path, so stamping it with the library version would
#: claim a reproducibility the data does not have — the repository that answered
#: yesterday can answer differently today and no version records that. A source
#: with no version gets no snapshot at all rather than a snapshot asserting
#: only what `pulse:platform` already says.
_INDEX_BACKED: frozenset[str] = frozenset(
    {
        "pulse:EPFLGraph",
        "pulse:ETHZResearchCollection",
        "pulse:HuggingFace",
        "pulse:Infoscience",
        "pulse:ORCID",
        "pulse:OpenAlex",
        "pulse:ROR",
        "pulse:RenkuLab",
        "pulse:SNSF",
        "pulse:SWISSUbase",
        "pulse:Zenodo",
    },
)


@lru_cache(maxsize=1)
def index_version() -> str | None:
    """The `open_pulse_sources` build that produced the indices, or None.

    One place, deliberately: the same pin `pyproject.toml` declares and
    `tests/v2/test_open_pulse_sources_pin.py` guards, read back through the
    installed distribution rather than re-parsed. A second copy of the version
    is how the graph and the code that produced it drift apart.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

        return version("open-pulse-sources")
    except (PackageNotFoundError, ImportError):
        return None


def source_snapshot_iri(platform: str, version: str) -> str:
    """One source, as of one index build.

    The version is in the IRI because that is what makes the node shared: two
    runs a month apart against the same build name the same snapshot, which is
    the distinction between "what the source was" and "what a run made of it".
    """
    return f"urn:pulse:snapshot:{platform.removeprefix('pulse:').lower()}:{version}"


def source_snapshot(platform: str) -> dict[str, Any] | None:
    """The `pulse:SourceSnapshot` an output `prov:used`, when there is one."""
    version = index_version()
    if platform not in _INDEX_BACKED or not version:
        return None
    return {
        "@id": source_snapshot_iri(platform, version),
        "@type": "pulse:SourceSnapshot",
        "schema:name": platform.removeprefix("pulse:").lower(),
        "schema:softwareVersion": version,
        "pulse:platform": platform,
    }


# --------------------------------------------------------------------------
# mapping tables: internal `_field` -> raw shape property
#
# Only properties the corresponding shape declares may appear here; the closed
# shapes reject anything else. A test asserts that against the TTL.
# --------------------------------------------------------------------------

#: Person/organization platform detail. Lands on the *profile*, not the entity:
#: a follower count is a fact about an account, not about a person.
_PROFILE_FIELDS: dict[str, str] = {
    "_avatar_url": "schema:image",
    "_blog": "schema:url",
    "_company": "pulse:company",
    "_followers_count": "pulse:followerCount",
    "_following_count": "pulse:followingCount",
    "_github_created_at": "schema:dateCreated",
    "_github_updated_at": "schema:dateModified",
    "_location": "pulse:location",
    "_public_repos": "pulse:publicRepositoryCount",
    "_bio": "pulse:biography",
    "_orcid_biography": "pulse:biography",
    "_twitter_username": "pulse:socialLink",
}

_ORGANIZATION_PROFILE_FIELDS: dict[str, str] = {
    "_followers_count": "pulse:followerCount",
    "_location": "pulse:location",
    "_description": "schema:description",
    "_blog": "schema:url",
    "_github_updated_at": "schema:dateModified",
}

#: Repository detail. `RawRepositoryShape` is closed and declares ~60
#: properties; these are the ones the providers actually populate today.
_REPOSITORY_FIELDS: dict[str, str] = {
    "_archived": "pulse:archived",
    "_default_branch": "pulse:defaultBranch",
    "_description": "schema:description",
    "_git_tag_count": "pulse:gitTagCount",
    "_has_discussions": "pulse:hasDiscussions",
    "_has_pages": "pulse:hasPages",
    "_has_wiki": "pulse:hasWiki",
    "_open_issues_count": "pulse:openIssueCount",
    "_primary_language": "pulse:language",
    "_pushed_at": "pulse:pushedDate",
    "_release_count": "pulse:releaseCount",
    # `_watchers_count` is deliberately absent: GitHub's `watchers_count` is
    # the *star* count (identical to `stargazers_count`), so mapping it to
    # `pulse:likeCount` just duplicates `pulse:repositoryStars`. The field
    # holding actual watchers is `subscribers_count`.
    "_subscribers_count": "pulse:watcherCount",
    "_updated_at": "schema:dateModified",
    "_homepage": "schema:url",
    "_code_of_conduct_url": "pulse:codeOfConduct",
    "_security_url": "pulse:securityPolicy",
    "_publiccode_url": "pulse:publicCodeManifest",
    "_keywords": "pulse:tag",
    "_size_kb": "pulse:fileCount",
}

#: Properties whose value is an IRI reference rather than a literal, so they
#: serialise as `{"@id": ...}`. Taken from the shapes' `sh:nodeKind sh:IRI`.
_IRI_VALUED: frozenset[str] = frozenset(
    {
        "schema:url",
        "schema:image",
        "pulse:codeOfConduct",
        "pulse:securityPolicy",
        "pulse:publicCodeManifest",
        "pulse:fundingConfig",
        "pulse:readme",
        "pulse:externalReference",
        "schema:license",
        "schema:citation",
    },
)

#: `pulse:VisibilityEnumeration` members, keyed by the GitHub API's wording.
_VISIBILITY: dict[str, str] = {
    "public": "pulse:PublicVisibility",
    "private": "pulse:PrivateVisibility",
    "internal": "pulse:InternalVisibility",
}


def _coerce(prop: str, value: Any) -> Any:
    if prop in _IRI_VALUED:
        return {"@id": value} if isinstance(value, str) else None
    return value


def _apply(target: dict[str, Any], node: Mapping[str, Any], table: Mapping[str, str]) -> None:
    for field, prop in table.items():
        value = node.get(field)
        if _is_empty(value):
            continue
        coerced = _coerce(prop, value)
        if not _is_empty(coerced):
            target[prop] = coerced


# --------------------------------------------------------------------------
# entity projections
# --------------------------------------------------------------------------

#: Carried straight through from the flat form, per type. The raw *entity*
#: shapes for Person and Organization are open, so an unmapped property is
#: tolerated there — but passing junk through would defeat the point of having
#: shapes, so these lists stay explicit.
_PASSTHROUGH: dict[str, tuple[str, ...]] = {
    "schema:Person": (
        "schema:name",
        "org:hasMembership",
        "pulse:hasContribution",
        "pulse:hasAuthoredArticle",
        "pulse:owns",
    ),
    "org:Organization": (
        "schema:name",
        "pulse:ror",
        "org:hasUnit",
        "org:unitOf",
        "pulse:organizationType",
        "pulse:owns",
    ),
    "schema:SoftwareSourceCode": (
        "schema:name",
        "schema:author",
        "schema:license",
        "schema:citation",
        "schema:dateCreated",
        "schema:programmingLanguage",
        "pulse:ownedBy",
        "pulse:isForkOf",
        "pulse:repositoryType",
        "pulse:discipline",
    ),
    "schema:ScholarlyArticle": (
        "schema:name",
        "schema:author",
        "schema:sourceOrganization",
        "schema:dateCreated",
    ),
    # A contribution is a raw fact too — GitHub reports `contributions` per
    # contributor per repository — so the properties have to survive. The
    # dates are `xsd:dateTime` in the shape while the flat form sometimes
    # carries a bare date; `_coerce` leaves them alone, so a bare date would
    # be a datatype violation the substrate gate reports rather than hides.
    "pulse:Contribution": (
        "pulse:contributionTo",
        "schema:author",
        "pulse:contributionCount",
        "pulse:firstContributionDate",
        "pulse:lastContributionDate",
        "pulse:gitAuthorName",
        "pulse:gitAuthorEmail",
    ),
    # A membership *is* a raw fact — ORCID asserts "X was employed at Y" — so
    # `RawMembershipShape` exists and the properties must survive. Omitting
    # this table entry emitted `<id> a org:Membership .` and nothing else,
    # which validated cleanly because the shape has no required properties.
    "org:Membership": (
        "org:organization",
        "org:role",
        "pulse:department",
        "pulse:membershipType",
        "pulse:qualification",
        "time:hasBeginning",
        "time:hasEnd",
    ),
}


def _base(node: Mapping[str, Any], node_type: str) -> dict[str, Any]:
    out: dict[str, Any] = {"@id": node["@id"], "@type": node_type}
    for key in _PASSTHROUGH.get(node_type, ()):
        if key in node and not _is_empty(node[key]):
            out[key] = node[key]
    return out


def _raw_profile(
    *,
    handle_value: Any,
    subject_iri: str,
    node: Mapping[str, Any],
    organization: bool,
) -> dict[str, Any] | None:
    """A `Raw*ProfileShape` node carrying everything the platform told us."""
    platform, instance, handle = _platform_for(handle_value)
    if platform is None or not handle:
        return None

    kind = "org-profile" if organization else "profile"
    profile: dict[str, Any] = {
        "@id": profile_iri(platform, handle, instance=instance, kind=kind),
        "@type": (
            "pulse:OrganizationProfile" if organization else "pulse:PlatformProfile"
        ),
        "pulse:platform": platform,
    }
    if organization:
        profile["pulse:organizationHandle"] = handle
        profile["pulse:organizationProfileOf"] = {"@id": subject_iri}
        _apply(profile, node, _ORGANIZATION_PROFILE_FIELDS)
    else:
        profile["pulse:platformUsername"] = [handle]
        profile["pulse:profileOf"] = {"@id": subject_iri}
        _apply(profile, node, _PROFILE_FIELDS)
        email = node.get("schema:email")
        if not _is_empty(email):
            profile["schema:email"] = email if isinstance(email, list) else [email]

    if instance:
        profile["pulse:platformInstance"] = {"@id": instance}
    return profile


_ROR_ID = re.compile(r"^https://ror\.org/[0-9a-z]{9}$")


def _ror_from(node: Mapping[str, Any]) -> str | None:
    """The organization's ROR, from its own id. `RawOrganizationShape` patterns
    the URL form, so it is returned as-is rather than made bare — `pulse:ror`
    is the deliberate exception to v3's bare-identifier rule (§2.4)."""
    iri = str(node.get("@id") or "")
    return iri if _ROR_ID.match(iri) else None


def _deposit(node: Mapping[str, Any], article_iri: str) -> dict[str, Any] | None:
    """The platform record an article was found in.

    Mirrors `canonical_projection._deposit` deliberately, including refusing to
    emit one without both `pulse:platform` and `schema:datePublished`: the
    canonical `DepositShape` requires both, so a partial deposit in the
    substrate would be a node that can never be promoted.
    """
    platform, instance, internal_id = _platform_for(
        node.get("pulse:infoscienceArticleIdentifier"),
    )
    published = node.get("schema:datePublished")
    if platform is None or not internal_id or _is_empty(published):
        return None

    deposit: dict[str, Any] = {
        "@id": deposit_iri(platform, internal_id),
        "@type": "pulse:Deposit",
        "pulse:platform": platform,
        "pulse:platformInternalId": internal_id,
        "schema:datePublished": published,
        "pulse:depositOf": {"@id": article_iri},
    }
    if instance:
        deposit["pulse:platformInstance"] = {"@id": instance}
    return deposit


def _project_person(node: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    out = _base(node, "schema:Person")
    profiles: list[dict[str, Any]] = []
    for key in ("pulse:githubUsername", "pulse:infosciencePersonIdentifier"):
        profile = _raw_profile(
            handle_value=node.get(key),
            subject_iri=out["@id"],
            node=node,
            organization=False,
        )
        if profile is not None:
            profiles.append(profile)

    orcid = bare_orcid(node.get("pulse:orcidIdentifier"))
    if orcid:
        out["pulse:orcidIdentifier"] = [orcid]
    if profiles:
        out["pulse:hasProfile"] = [{"@id": p["@id"]} for p in profiles]
    return out, profiles


def _project_organization(node: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    out = _base(node, "org:Organization")

    # Re-derive `pulse:ror` from the id when the id *is* a ROR.
    #
    # `build_jsonld_output` strips the field in that case — the `@id` already
    # carries it and the v2 Organization shape is closed against it — so the
    # flat form the substrate projects from has no `pulse:ror` at all. The
    # canonical projection re-derives it (§3f, the 50% -> 85% fix); the raw
    # projection did not, and the consequences only showed up when the unifier
    # read the substrate: 40 of 102 organizations failed `OrganizationShape`'s
    # identity `sh:or`, and the ROR match key — the *primary* one for
    # organizations — never fired once, because the field was not there.
    ror = _ror_from(node)
    if ror and _is_empty(out.get("pulse:ror")):
        out["pulse:ror"] = ror

    profiles: list[dict[str, Any]] = []
    for key in (
        "pulse:githubOrganizationHandle",
        "pulse:infoscienceOrganizationIdentifier",
    ):
        profile = _raw_profile(
            handle_value=node.get(key),
            subject_iri=out["@id"],
            node=node,
            organization=True,
        )
        if profile is not None:
            profiles.append(profile)
    if not profiles:
        profile = _raw_profile(
            handle_value=out["@id"],
            subject_iri=out["@id"],
            node=node,
            organization=True,
        )
        if profile is not None:
            profiles.append(profile)
    if profiles:
        out["pulse:hasOrganizationProfile"] = [{"@id": p["@id"]} for p in profiles]
    return out, profiles


def _project_repository(node: Mapping[str, Any]) -> dict[str, Any]:
    out = _base(node, "schema:SoftwareSourceCode")
    raw_handle = node.get("pulse:githubRepositoryHandle")
    handle = bare_handle(raw_handle)
    if handle:
        out["pulse:repositoryHandle"] = handle
    platform, _instance, _first = _platform_for(raw_handle)
    if platform is None:
        platform, _instance, _first = _platform_for(out["@id"])
    if platform:
        out["pulse:platform"] = platform

    for old_key, new_key in (
        ("pulse:githubRepoStars", "pulse:repositoryStars"),
        ("pulse:githubRepoForks", "pulse:repositoryForks"),
    ):
        if not _is_empty(node.get(old_key)):
            out[new_key] = node[old_key]

    _apply(out, node, _REPOSITORY_FIELDS)

    visibility = _VISIBILITY.get(str(node.get("_visibility") or "").lower())
    if visibility:
        out["pulse:visibility"] = visibility
    return out


def _project_contribution(node: Mapping[str, Any]) -> dict[str, Any]:
    """One platform's report of a person's commits on a repository.

    Carries no `pulse:partOfRun`: `RawContributionShape` declares none, for the
    same reason `RawMembershipShape` does not — the anchor belongs to entities
    a source describes, not to the edges between them. The named graph it is
    written into is its attribution, which is why
    `substrate._NAMES_SUBJECT` has to route it.
    """
    return _base(node, "pulse:Contribution")


def _project_article(node: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    """An article plus the platform record it was found in.

    The deposit is minted **here** and not only in the canonical projection,
    which is where it was until unification tried to build canonical from the
    substrate and found 26 of 26 articles missing a required
    `pulse:hasDeposit`. The substrate is the durable layer: anything the
    canonical layer needs and the substrate does not hold is unrecoverable
    from the store, however conformant the raw graph looks.

    `RawArticleShape` declares `pulse:hasDeposit`, so this was always the
    intended home; it just had no consumer. Note that raw conformance stayed at
    119/119 without it — `RawArticleShape` has no `sh:minCount` on the property
    — which is why measuring the raw layer against the *raw* shapes never
    caught it. Well-formed and sufficient are different questions.
    """
    out = _base(node, "schema:ScholarlyArticle")
    doi = bare_doi(node.get("schema:identifier") or node.get("pulse:doi"))
    if doi:
        out["pulse:doi"] = [doi]
    keywords = node.get("_keywords")
    if not _is_empty(keywords):
        out["pulse:keyword"] = keywords

    deposit = _deposit(node, out["@id"])
    if deposit is not None:
        out["pulse:hasDeposit"] = [{"@id": deposit["@id"]}]
        return out, [deposit]
    return out, []


_PAIR = {
    "schema:Person": _project_person,
    "org:Organization": _project_organization,
    # Articles mint a companion node too, since the deposit moved here.
    "schema:ScholarlyArticle": _project_article,
}
_SINGLE = {
    "schema:SoftwareSourceCode": _project_repository,
    "org:Membership": lambda n: _base(n, "org:Membership"),
    "pulse:Contribution": _project_contribution,
}

#: Types the substrate deliberately does not carry. Empty, and worth knowing
#: why it exists at all.
#:
#: `pulse:Contribution` was here, on the reasoning that a contribution is a
#: *derived* edge because "we compute commit counts". **That was wrong about
#: the count**: `agents/rule_based/contribution_agent.py` reads GitHub's
#: `contributions` field, so the number is something a platform *reports*, and
#: it belongs in the layer that records what sources said. Two further facts
#: settled it (2026-09-09):
#:
#: - `RawPersonShape` already declares `pulse:hasContribution` with
#:   `sh:class pulse:Contribution`, so the raw layer expects these nodes to
#:   exist — while no raw shape targeted the class, making it the one entity
#:   type the raw layer referenced and could not validate.
#: - The canonical `ContributionShape` requires `pulse:contributionCount`
#:   (`sh:minCount 1`), so the unifier cannot assert an edge without one. With
#:   the count absent from the substrate there was nothing to assert, and the
#:   store-side canonical graph had **no contributions at all** while
#:   `/v2/extract` returned 46.
#:
#: Ontology patch 06 (upstreamed; see ontology/patches/README.md) added the
#: missing shape.
#: What remains derived is the *aggregate* across platforms and runs, which is
#: the canonical node's business and is where the provenance record goes.
#:
#: Kept as an (empty) set rather than deleted: the distinction it encodes is
#: real and the next derived type — something the unifier computes rather than
#: reads — belongs here.
_DERIVED_TYPES: frozenset[str] = frozenset()


#: Hosts whose ids name a **registry** rather than an account-hosting platform.
#:
#: Deliberately separate from `canonical_projection._PLATFORM_BY_HOST`: adding
#: `ror.org` there would mint an `OrganizationProfile` for every ROR-identified
#: organization, and a ROR id is an identity, not an account — there is no
#: profile page, no handle, no follower count. What is needed here is only the
#: provenance anchor: which source said this.
#:
#: Without an entry, a ROR-identified organization has no platform, so no
#: `pulse:ExtractionOutput`, so no `pulse:partOfRun` — and it fell through into
#: the graph that describes the extraction, as though the registry's name for
#: it were a fact about the run. `pulse:ROR` came from ontology patch 05
#: (upstreamed; see ontology/patches/README.md); see §2.7 of
#: ONTOLOGY_V3_REQUIREMENTS.md for why the enumeration is the right home.
_REGISTRY_PLATFORM_BY_HOST: dict[str, str] = {
    "ror.org": "pulse:ROR",
}

#: Flat-form fields whose **value** is a URL on the source that asserted the
#: entity, for entities that cannot carry a platform themselves.
#:
#: Articles are the only case, and the list is deliberately not "every
#: Infoscience identifier field". `RawArticleShape` is `sh:closed` and declares
#: no `pulse:platform`, and an article's id is its DOI — `doi.org` is a
#: resolver, not the repository that holds the record. So the projected node
#: offers no platform signal at all, and every article landed in the graph
#: reserved for extraction metadata. The Infoscience URL it was found through
#: is the signal, and it exists only on the flat node.
#:
#: The person and organization equivalents were in this table until tracing
#: which signal decided each anchor across the corpus showed they never fire:
#: a person or organization carrying an Infoscience URL gets an Infoscience
#: *profile* from the same value, and the profile is checked first. Only the
#: article field ever reaches here — 26 times per corpus pass, one per article.
_SOURCE_URL_FIELDS: tuple[str, ...] = ("pulse:infoscienceArticleIdentifier",)


#: Only these raw shapes declare `pulse:partOfRun`. `RawMembershipShape` is
#: closed and does not, so stamping every entity with it makes memberships
#: violate — the anchor belongs to the entities a source describes, not to the
#: edges between them, which are derived.
_PART_OF_RUN_TYPES: frozenset[str] = frozenset(
    {
        "schema:Person",
        "org:Organization",
        "schema:SoftwareSourceCode",
        "schema:ScholarlyArticle",
    },
)


def _registry_platform(iri: Any) -> str | None:
    """The registry that minted this id, for entities held by no platform."""
    host = _host_and_path(iri)[0]
    return _REGISTRY_PLATFORM_BY_HOST.get(host) if host else None


def _platform_of(
    entity: Mapping[str, Any],
    profiles: list[dict[str, Any]],
    source: Mapping[str, Any],
) -> str | None:
    """Which platform slice this entity belongs to.

    Five signals, in descending order of directness: its own profile (that is
    the account the data came from), a `pulse:platform` already on the entity
    (repositories carry one directly), the id read as a platform URL, the id
    read as a registry id, and finally a source URL on the flat node.

    The last two are fallbacks for entities no platform holds an account for,
    not re-attributions: an organization discovered through its GitHub org and
    *then* resolved to a ROR keeps the GitHub anchor, because GitHub is what
    asserted most of the properties the node carries.

    This is the *anchor*, no longer the whole answer. `source_attribution`
    splits the node into one slice per asserting source and leaves on this
    anchor only what it cannot attribute — so the ROR in the example above now
    lands in the ROR slice while the GitHub-described properties stay here.
    """
    if profiles:
        from_profile = profiles[0].get("pulse:platform")
        if from_profile:
            return str(from_profile)
    declared = entity.get("pulse:platform")
    if declared:
        return str(declared)
    platform, _instance, _first = _platform_for(entity["@id"])
    if platform:
        return platform
    registry = _registry_platform(entity["@id"])
    if registry:
        return registry
    for field in _SOURCE_URL_FIELDS:
        platform, _instance, _first = _platform_for(source.get(field))
        if platform:
            return platform
    return None


def _slices(
    out: Mapping[str, Any],
    profiles: list[dict[str, Any]],
    *,
    anchor: str,
) -> list[tuple[str, dict[str, Any]]]:
    """`split_by_source`, with the empty case folded back onto the anchor.

    An entity carrying nothing but `@id` and `@type` splits into no slices at
    all, because every slice is identity-only and `split_by_source` drops
    those. Emitting nothing would delete the entity from the substrate — and a
    bare node is still a source saying "this exists", which is exactly the kind
    of assertion the raw layer is for.
    """
    split = [
        (platform, node)
        for platform, node in split_by_source(out, profiles, anchor=anchor)
        if platform is not None
    ]
    return split or [(anchor, dict(out))]


def _project_one(
    node: Mapping[str, Any],
    node_type: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Project one node, or None when the substrate should not carry it."""
    if node_type in _DERIVED_TYPES:
        return None
    if node_type in _PAIR:
        return _PAIR[node_type](node)
    if node_type in _SINGLE:
        return _SINGLE[node_type](node), []
    return None


def _output_for(  # noqa: PLR0913 — one keyword per piece of run context
    platform: str,
    *,
    outputs: dict[str, dict[str, Any]],
    snapshots: dict[str, dict[str, Any]],
    run_id: str,
    run_iri: str,
    generated_at: str | None,
) -> dict[str, Any]:
    """The `pulse:ExtractionOutput` for `platform`, minting it on first use.

    Mints the platform's `pulse:SourceSnapshot` at the same time, because the
    two are decided together: an output points at a snapshot only when the
    source it names has a version, and that is a property of the platform
    rather than of the entity that happened to arrive first.
    """
    existing = outputs.get(platform)
    if existing is not None:
        return existing
    snapshot = source_snapshot(platform)
    if snapshot is not None:
        snapshots[snapshot["@id"]] = snapshot
    output = extraction_output(
        run_id=run_id,
        platform=platform,
        run_iri=run_iri,
        generated_at=generated_at,
        snapshot_iri=snapshot["@id"] if snapshot else None,
    )
    outputs[platform] = output
    return output


def project_raw(
    nodes: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
    run_nodes: Iterable[Mapping[str, Any]] = (),
    run_iri: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Project entities into the raw shapes, grouped by extraction output.

    Every raw entity gets `pulse:partOfRun` → the `pulse:ExtractionOutput` for
    the source that asserted it, and each output links to the run. That chain
    is what lets a canonical triple be traced back to the source that asserted
    it.

    An entity with more than one source is emitted **once per source**, with
    the properties partitioned between the copies by `source_attribution`.
    Same `@id` in several named graphs is the substrate's normal shape — it is
    what two runs over one repository already produce — and `unify.cluster`
    collapses them by IRI without a vote.

    `run_nodes` is the `ExtractionRun` (and its `SoftwareAgent`) as produced by
    `extraction_run.build_extraction_run`, and they are **included in the
    output graph** rather than merely referenced. `ExtractionOutputShape`
    constrains `prov:wasGeneratedBy` with `sh:class pulse:ExtractionRun`, which
    can only resolve if the run node is in the graph being validated — a
    dangling reference is a violation, not a forward declaration. It is also
    what makes a substrate graph self-describing once loaded into the store.
    """
    run_nodes = list(run_nodes)
    if run_iri is None:
        run_iri = next(
            (
                str(node["@id"])
                for node in run_nodes
                if str(node.get("@type")) == "pulse:ExtractionRun"
            ),
            f"urn:pulse:run:{run_id}",
        )
    projected: list[dict[str, Any]] = []
    minted: dict[str, dict[str, Any]] = {}
    outputs: dict[str, dict[str, Any]] = {}
    snapshots: dict[str, dict[str, Any]] = {}
    skipped: dict[str, int] = {}

    for node in nodes:
        if not isinstance(node, dict) or not node.get("@id"):
            continue
        node_type = str(node.get("@type") or "")
        projected_pair = _project_one(node, node_type)
        if projected_pair is None:
            key = node_type or "(untyped)"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        out, profiles = projected_pair

        anchor = _platform_of(out, profiles, node)
        if anchor is None or node_type not in _PART_OF_RUN_TYPES:
            projected.append(out)
        else:
            # One node per asserting source. `split_by_source` leaves anything
            # it cannot attribute on the anchor slice, so an entity with a
            # single source comes back as the single node it always was.
            for platform, node_slice in _slices(out, profiles, anchor=anchor):
                output = _output_for(
                    platform,
                    outputs=outputs,
                    snapshots=snapshots,
                    run_id=run_id,
                    run_iri=run_iri,
                    generated_at=generated_at,
                )
                node_slice["pulse:partOfRun"] = {"@id": output["@id"]}
                projected.append(node_slice)

        for profile in profiles:
            minted[profile["@id"]] = profile

    if skipped:
        logger.info("raw_projection: skipped unmapped types %s", skipped)

    return {
        "@graph": [
            *projected,
            *minted.values(),
            *outputs.values(),
            *snapshots.values(),
            *run_nodes,
        ],
    }


__all__ = [
    "extraction_output",
    "extraction_output_iri",
    "index_version",
    "project_raw",
    "source_snapshot",
    "source_snapshot_iri",
]
