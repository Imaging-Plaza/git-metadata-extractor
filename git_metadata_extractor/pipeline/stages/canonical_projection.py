"""Project the v2 output graph into the v3 canonical shapes.

`PROVENANCE_ARCHITECTURE.md` phase 2's larger half: the platform-profile
identity model. Today a Person carries a flat `pulse:githubUsername`; v3 wants
`pulse:hasProfile` → a `pulse:PlatformProfile` node, because the store-side
unifier (phase 4) links *profiles* to canonical Persons and cannot do that
against a flat string.

**Gated off by default** (`V2_CANONICAL_OUTPUT_ENABLED`). This runs alongside
the v2 output rather than replacing it, for one reason: the v3 canonical shapes
are `sh:closed`, so the projection is either right or produces a graph that
validates as neither v2 nor v3. Building it beside the real output lets the
projection be validated against the actual v3 SHACL — see
`scripts/v2/canonical_conformance.py` — and the flip becomes a default change
plus a re-baseline once the conformance number is understood.

**What this drops, and why that is a gap in *this module*, not the ontology:**

1. Follower counts, biographies, locations, avatars and homepages have no slot
   on the closed canonical shapes — but `RawPlatformProfileShape` declares
   `pulse:followerCount`, `pulse:biography`, `pulse:location`,
   `pulse:company`, `pulse:socialLink`, `schema:image` and `schema:url`. They
   belong to the **raw** layer. This module projects flat-v2 straight to
   canonical in one hop, skipping raw, so it has nowhere to put them. Emitting
   raw is the fix, not an ontology change.
2. A Person's `schema:url` is a case of the above: it moves to a matching
   `PlatformProfile` when the host matches one, and is otherwise dropped.
3. An Article with no platform record gets no `pulse:Deposit`, and
   `ArticleShape` requires one. In the 120-repo corpus every article carries an
   Infoscience identifier and a publication date, so all 26 get a deposit — but
   a DOI-only article would still be non-conformant, with nowhere to put the
   date either (`ArticleShape` has no `schema:datePublished`).

Value formats change too, per the confirmed migration: `pulse:orcidIdentifier`,
`pulse:doi` and `pulse:repositoryHandle` go bare, while `pulse:ror` stays a URL
(§2.4 of ONTOLOGY_V3_REQUIREMENTS.md).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)

#: host -> (`pulse:PlatformEnumeration` member, where the handle sits in the
#: path). Only hosts we can actually recognise; an unknown host yields no
#: profile rather than a guessed platform.
#:
#: The handle position is not cosmetic. A GitHub profile is
#: `github.com/{handle}` — first segment — but an Infoscience entity is
#: `infoscience.epfl.ch/entities/{kind}/{uuid}`, where the first segment is the
#: literal string "entities". Taking the first segment there produces a profile
#: whose `pulse:platformUsername` is `"entities"`, which satisfies every SHACL
#: constraint while being meaningless — a silent data bug no validation would
#: have caught.
_PLATFORM_BY_HOST: dict[str, tuple[str, int]] = {
    "github.com": ("pulse:GitHub", 0),
    "gitlab.com": ("pulse:GitLab", 0),
    "bitbucket.org": ("pulse:Bitbucket", 0),
    "huggingface.co": ("pulse:HuggingFace", 0),
    "orcid.org": ("pulse:ORCID", -1),
    "zenodo.org": ("pulse:Zenodo", -1),
    "infoscience.epfl.ch": ("pulse:Infoscience", -1),
}

#: Hosts that are *the* public instance of their platform. A profile on any
#: other host of the same platform needs `pulse:platformInstance` to be
#: distinguishable — the whole point of patch 01.
_DEFAULT_HOSTS: frozenset[str] = frozenset(_PLATFORM_BY_HOST)

_ORCID_BARE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[0-9X]$")
_DOI_BARE = re.compile(r"^10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+$")


def _clean(value: Any) -> Any:
    """Drop the nulls and empty collections the v2 output is full of.

    v2 emits `"schema:citation": null` and `"pulse:discipline": []`; both are
    closed-shape violations in v3 if carried through as keys, and neither says
    anything.
    """
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if not _is_empty(v)}
    if isinstance(value, list):
        return [_clean(v) for v in value if not _is_empty(v)]
    return value


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, (list, dict, str)) and not value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _iri(value: Any) -> str | None:
    """The IRI behind either `"x"` or `{"@id": "x"}`."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        got = value.get("@id")
        return got if isinstance(got, str) and got else None
    return None


def _host_and_path(value: Any) -> tuple[str | None, list[str]]:
    iri = _iri(value)
    if not iri:
        return None, []
    parsed = urlparse(iri)
    host = (parsed.netloc or "").lower().removeprefix("www.")
    segments = [s for s in parsed.path.split("/") if s]
    return (host or None), segments


def bare_orcid(value: Any) -> str | None:
    """`https://orcid.org/0000-...` -> `0000-...`, or None if unrecognisable."""
    iri = _iri(value)
    if not iri:
        return None
    candidate = iri.rstrip("/").rsplit("/", maxsplit=1)[-1].upper()
    return candidate if _ORCID_BARE.match(candidate) else None


def bare_doi(value: Any) -> str | None:
    """`https://doi.org/10.x/y` -> `10.x/y`."""
    iri = _iri(value)
    if not iri:
        return None
    candidate = iri
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if candidate.lower().startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    return candidate if _DOI_BARE.match(candidate) else None


def bare_handle(value: Any) -> str | None:
    """`https://github.com/owner/name` -> `owner/name`; `owner/name` unchanged.

    Host-agnostic on purpose: a self-hosted GitLab repository handle is still
    `group/project`, and hardcoding `github.com` was a bug in the v2 code this
    replaces.
    """
    iri = _iri(value)
    if not iri:
        return None
    if "://" not in iri:
        return iri.strip("/") or None
    _host, segments = _host_and_path(iri)
    return "/".join(segments) or None


def profile_iri(
    platform: str,
    handle: str,
    *,
    instance: str | None = None,
    kind: str = "profile",
) -> str:
    """Stable, readable IRI for a platform or organization profile.

    Two segments carry identity beyond the handle, and both are load-bearing:

    - **`instance`**, when the host is not the platform's public one. `epfl` on
      gitlab.epfl.ch and `epfl` on gitlab.com are two organizations, and
      collapsing them is the bug patch 01 exists to prevent.
    - **`kind`**, because a `PlatformProfile` and an `OrganizationProfile` can
      share a platform *and* a handle. This pipeline routinely produces both
      for one GitHub org — a Person stub for the repo owner and the
      Organization itself. With one IRI for both, the second minted node
      silently replaced the first and left the organization pointing at a node
      of the wrong class, which `sh:class pulse:OrganizationProfile` rejects.
    """
    slug = platform.removeprefix("pulse:").lower()
    parts = ["urn:pulse", kind, slug]
    if instance:
        parts.append(instance)
    parts.append(handle)
    return ":".join(parts)


def _platform_for(value: Any) -> tuple[str | None, str | None, str | None]:
    """(platform, instance-or-None, first path segment) for a profile URL."""
    host, segments = _host_and_path(value)
    if not host or not segments:
        return None, None, None
    entry = _PLATFORM_BY_HOST.get(host)
    if entry is None:
        # A host we do not recognise: guessing the platform would put a wrong
        # `pulse:platform` on a closed shape, which is worse than no profile.
        return None, None, None
    platform, index = entry
    instance = None if host in _DEFAULT_HOSTS else f"https://{host}"
    return platform, instance, segments[index]


def _platform_profile(
    *,
    handle_value: Any,
    subject_iri: str,
    urls: Iterable[Any] = (),
) -> dict[str, Any] | None:
    """One `pulse:PlatformProfile` for a person's platform identity."""
    platform, instance, handle = _platform_for(handle_value)
    if platform is None or not handle:
        return None

    profile: dict[str, Any] = {
        "@id": profile_iri(platform, handle, instance=instance),
        "@type": "pulse:PlatformProfile",
        "pulse:platform": platform,
        "pulse:platformUsername": [handle],
        "pulse:profileOf": {"@id": subject_iri},
    }
    if instance:
        profile["pulse:platformInstance"] = {"@id": instance}

    # A person's `schema:url` has no slot on the closed PersonShape, so it
    # lands on the profile whose host it matches. Anything else is dropped —
    # recorded as a known loss in the module docstring rather than forced into
    # a property that does not mean it.
    matching = [
        u for u in urls if _host_and_path(u)[0] == _host_and_path(handle_value)[0]
    ]
    if matching:
        profile["schema:url"] = [{"@id": _iri(u)} for u in matching if _iri(u)]
    return profile


def _organization_profile(
    *,
    handle_value: Any,
    subject_iri: str,
) -> dict[str, Any] | None:
    """One `pulse:OrganizationProfile`. `pulse:organizationHandle` is required."""
    platform, instance, handle = _platform_for(handle_value)
    if platform is None or not handle:
        return None
    profile: dict[str, Any] = {
        "@id": profile_iri(platform, handle, instance=instance, kind="org-profile"),
        "@type": "pulse:OrganizationProfile",
        "pulse:platform": platform,
        "pulse:organizationHandle": handle,
        "pulse:organizationProfileOf": {"@id": subject_iri},
    }
    if instance:
        profile["pulse:platformInstance"] = {"@id": instance}
    return profile


# --------------------------------------------------------------------------
# per-type projections
# --------------------------------------------------------------------------

#: Properties carried straight through, per type. Everything absent from these
#: lists is either renamed below or deliberately dropped.
_PASSTHROUGH: Mapping[str, tuple[str, ...]] = {
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
    ),
    "org:Membership": (
        "org:organization",
        "org:role",
        "time:hasBeginning",
        "time:hasEnd",
    ),
    "pulse:Contribution": (
        "schema:author",
        "pulse:contributionTo",
        "pulse:contributionCount",
        "pulse:firstContributionDate",
        "pulse:lastContributionDate",
        "pulse:gitAuthorName",
        "pulse:gitAuthorEmail",
    ),
}


def _base(node: Mapping[str, Any], node_type: str) -> dict[str, Any]:
    out: dict[str, Any] = {"@id": node["@id"], "@type": node_type}
    for key in _PASSTHROUGH.get(node_type, ()):
        if key in node and not _is_empty(node[key]):
            out[key] = node[key]
    return out


def _project_person(node: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    out = _base(node, "schema:Person")
    subject = out["@id"]
    profiles: list[dict[str, Any]] = []

    urls = _as_list(node.get("schema:url"))
    for key in ("pulse:githubUsername", "pulse:infosciencePersonIdentifier"):
        profile = _platform_profile(
            handle_value=node.get(key),
            subject_iri=subject,
            urls=urls,
        )
        if profile is not None:
            profiles.append(profile)

    orcid = bare_orcid(node.get("pulse:orcidIdentifier"))
    if orcid:
        out["pulse:orcidIdentifier"] = [orcid]
    if profiles:
        out["pulse:hasProfile"] = [{"@id": p["@id"]} for p in profiles]
    return out, profiles


_ROR_ID = re.compile(r"^https://ror\.org/[0-9a-z]{9}$")


def _project_organization(node: Mapping[str, Any]) -> tuple[dict, list[dict]]:
    out = _base(node, "org:Organization")

    # Re-derive `pulse:ror` from the id when the id *is* the ROR. v2's
    # `build_jsonld_output` strips a redundant `pulse:ror` in exactly that
    # case, because the v2.1.2 Organization shape was closed against it. v3
    # inverts the requirement: the identity `sh:or` is
    # `pulse:ror | pulse:hasOrganizationProfile`, so a ROR-identified
    # organization with the property stripped has no identity at all and
    # fails validation. Without this, every ROR org in the corpus violates.
    if "pulse:ror" not in out and _ROR_ID.match(str(out["@id"])):
        out["pulse:ror"] = [out["@id"]]
    profiles: list[dict[str, Any]] = []
    for key in (
        "pulse:githubOrganizationHandle",
        "pulse:infoscienceOrganizationIdentifier",
    ):
        profile = _organization_profile(
            handle_value=node.get(key),
            subject_iri=out["@id"],
        )
        if profile is not None:
            profiles.append(profile)

    # Fall back to the id itself when no handle property carried an identity.
    # An Infoscience org unit arrives with its `@id` *being* the identity and
    # nothing else to go on; without this it has neither `pulse:ror` nor a
    # profile and fails the OrganizationShape identity `sh:or`.
    if not profiles and "pulse:ror" not in out:
        profile = _organization_profile(
            handle_value=out["@id"],
            subject_iri=out["@id"],
        )
        if profile is not None:
            profiles.append(profile)

    if profiles:
        out["pulse:hasOrganizationProfile"] = [
            {"@id": p["@id"]} for p in profiles
        ]
    # `pulse:githubOrgFollowers` has no v3 property; see the module docstring.
    return out, profiles


def _project_repository(node: Mapping[str, Any]) -> dict[str, Any]:
    out = _base(node, "schema:SoftwareSourceCode")
    raw_handle = node.get("pulse:githubRepositoryHandle")
    handle = bare_handle(raw_handle)
    if handle:
        out["pulse:repositoryHandle"] = handle
    platform, instance, _first = _platform_for(raw_handle)
    # `pulse:platform` is required on RepositoryShape. Fall back to the id when
    # the handle is missing, since the id is a platform URL too.
    if platform is None:
        platform, instance, _first = _platform_for(out["@id"])
    if platform:
        out["pulse:platform"] = platform
    if instance:
        out["pulse:platformInstance"] = {"@id": instance}

    for old, new in (
        ("pulse:githubRepoStars", "pulse:repositoryStars"),
        ("pulse:githubRepoForks", "pulse:repositoryForks"),
    ):
        if not _is_empty(node.get(old)):
            out[new] = node[old]
    return out


def deposit_iri(platform: str, internal_id: str) -> str:
    slug = platform.removeprefix("pulse:").lower()
    return f"urn:pulse:deposit:{slug}:{internal_id}"


def _deposit(node: Mapping[str, Any], article_iri: str) -> dict[str, Any] | None:
    """The platform record an article was found in.

    v3 puts `schema:datePublished` on the deposit, not the article: the
    abstract work has no single publication date, its platform records do.
    `ArticleShape` has no `schema:datePublished` at all, so the date is lost
    unless it moves here.

    Both `pulse:platform` and `schema:datePublished` are required, so a record
    missing either yields no deposit rather than an invalid one.
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


def _project_article(node: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    out = _base(node, "schema:ScholarlyArticle")
    doi = bare_doi(node.get("schema:identifier") or node.get("pulse:doi"))
    if doi:
        out["pulse:doi"] = [doi]

    deposit = _deposit(node, out["@id"])
    if deposit is not None:
        out["pulse:hasDeposit"] = [{"@id": deposit["@id"]}]
    return out, ([deposit] if deposit is not None else [])


#: Types projected without minting any companion nodes.
_PROJECTORS = {
    "schema:SoftwareSourceCode": _project_repository,
    "org:Membership": lambda n: _base(n, "org:Membership"),
    "pulse:Contribution": lambda n: _base(n, "pulse:Contribution"),
}

#: Types that mint companion nodes (profiles, deposits) alongside themselves.
_PAIR_PROJECTORS = {
    "schema:Person": _project_person,
    "org:Organization": _project_organization,
    "schema:ScholarlyArticle": _project_article,
}


def project_canonical(nodes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Project a v2 `@graph` into the v3 canonical shapes.

    Returns a JSON-LD document. Profiles are minted as their own nodes, so the
    output is larger than the input in node count — that is the point of the
    model, not a bug.
    """
    projected: list[dict[str, Any]] = []
    minted: dict[str, dict[str, Any]] = {}
    skipped: dict[str, int] = {}

    for node in nodes:
        if not isinstance(node, dict) or not node.get("@id"):
            continue
        node_type = str(node.get("@type") or "")
        if node_type in _PAIR_PROJECTORS:
            out, profiles = _PAIR_PROJECTORS[node_type](node)
        elif node_type in _PROJECTORS:
            out, profiles = _PROJECTORS[node_type](node), []
        else:
            skipped[node_type or "(untyped)"] = skipped.get(node_type or "(untyped)", 0) + 1
            continue
        projected.append(_clean(out))
        for profile in profiles:
            # Two people can hold the same profile IRI only if they are the
            # same identity; last write wins and the graph stays single-noded.
            minted[profile["@id"]] = _clean(profile)

    if skipped:
        logger.info("canonical_projection: skipped unmapped types %s", skipped)

    return {"@graph": [*projected, *minted.values()]}


__all__ = [
    "bare_doi",
    "bare_handle",
    "bare_orcid",
    "deposit_iri",
    "profile_iri",
    "project_canonical",
]
