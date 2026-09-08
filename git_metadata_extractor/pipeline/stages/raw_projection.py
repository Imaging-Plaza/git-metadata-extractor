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
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.pipeline.stages.canonical_projection import (
    _is_empty,
    _platform_for,
    bare_doi,
    bare_handle,
    bare_orcid,
    profile_iri,
)

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
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "@id": extraction_output_iri(run_id, platform),
        "@type": "pulse:ExtractionOutput",
        "pulse:platform": platform,
        "prov:wasGeneratedBy": {"@id": run_iri},
    }
    if generated_at:
        node["prov:generatedAtTime"] = generated_at
    return node


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


def _project_article(node: Mapping[str, Any]) -> dict[str, Any]:
    out = _base(node, "schema:ScholarlyArticle")
    doi = bare_doi(node.get("schema:identifier") or node.get("pulse:doi"))
    if doi:
        out["pulse:doi"] = [doi]
    keywords = node.get("_keywords")
    if not _is_empty(keywords):
        out["pulse:keyword"] = keywords
    return out


_PAIR = {
    "schema:Person": _project_person,
    "org:Organization": _project_organization,
}
_SINGLE = {
    "schema:SoftwareSourceCode": _project_repository,
    "schema:ScholarlyArticle": _project_article,
    "org:Membership": lambda n: _base(n, "org:Membership"),
}

#: Types the substrate deliberately does not carry.
#:
#: `pulse:Contribution` has **no raw shape at all** — and that is not an
#: oversight in the ontology, it is the architecture: a contribution is a
#: *derived* edge (we compute commit counts), so it is asserted by the unifier
#: into the canonical graph and gets provenance there. Writing one into the
#: substrate would put a computed fact in the layer reserved for what sources
#: said. Compare `org:Membership`, which *does* have a raw shape, because ORCID
#: genuinely asserts employment.
_DERIVED_TYPES: frozenset[str] = frozenset({"pulse:Contribution"})


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


def _platform_of(
    entity: Mapping[str, Any],
    profiles: list[dict[str, Any]],
) -> str | None:
    """Which platform slice this entity belongs to.

    Its own profile first (that is the account the data came from), then a
    `pulse:platform` already on the entity (repositories carry one directly),
    then the id as a last resort.
    """
    if profiles:
        from_profile = profiles[0].get("pulse:platform")
        if from_profile:
            return str(from_profile)
    declared = entity.get("pulse:platform")
    if declared:
        return str(declared)
    platform, _instance, _first = _platform_for(entity["@id"])
    return platform


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
    its platform, and each output links to the run. That chain is what lets a
    canonical triple be traced back to the source that asserted it.

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

        platform = _platform_of(out, profiles)
        if platform and node_type in _PART_OF_RUN_TYPES:
            if platform not in outputs:
                outputs[platform] = extraction_output(
                    run_id=run_id,
                    platform=platform,
                    run_iri=run_iri,
                    generated_at=generated_at,
                )
            out["pulse:partOfRun"] = {"@id": outputs[platform]["@id"]}

        projected.append(out)
        for profile in profiles:
            minted[profile["@id"]] = profile

    if skipped:
        logger.info("raw_projection: skipped unmapped types %s", skipped)

    return {
        "@graph": [*projected, *minted.values(), *outputs.values(), *run_nodes],
    }


__all__ = [
    "extraction_output",
    "extraction_output_iri",
    "project_raw",
]
