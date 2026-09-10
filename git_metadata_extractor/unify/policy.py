"""What the unifier is allowed to decide, per entity type and per property.

`PROVENANCE_ARCHITECTURE.md` splits unification into a **type-agnostic layer**
(clustering, merging, upsert, validation) and a **thin per-type identity
resolver** (canonical id, match keys). This module is the second half — the only
part that varies by type — plus the per-property dispositions the merger needs.

**Why the dispositions cannot be read off the shapes.** The obvious move is to
treat `sh:maxCount 1` as "pick a winner" and its absence as "union". It does not
work in one direction: 45 of the 78 canonical properties carry no cap, and among
them `schema:name`, `pulse:ror`, `pulse:orcidIdentifier`, `schema:license` and
`schema:dateCreated` are semantically single-valued while `pulse:owns`,
`org:hasUnit` and `pulse:hasProfile` are genuinely many. That is the direct cost
of the accepted-as-is cardinality decision (§3c of `REFACTOR_HANDOFF.md`, which
said to budget for it in the phase that consumes the models — this one).

It does work in the *other* direction, and that asymmetry is the guard:
`sh:maxCount 1` means a union would produce data the shapes reject, so a capped
property may never be `UNION`. `tests/v2/test_unify_policy.py` asserts that
against the real TTL, and asserts that every uncapped canonical property has an
explicit entry here — so a submodule bump that adds one fails loudly instead of
silently defaulting.

**What the corpus says the payoff is.** Measured over 119 real substrates
(2026-09-09): 17 entities appear in more than one run, 44 of their properties
agree and 12 differ. Nine of those twelve are `pulse:partOfRun` — per-run by
design, not a conflict. The other three are `pulse:owns`, `org:hasUnit` and
`pulse:hasOrganizationProfile`: **set accumulation, not contested values.**
There is not one genuine value conflict in the corpus.

So `UNION` is where the value is today — EPFL's ten units are scattered across
ten runs and only the union knows it has ten — and `SELECT` is machinery that
this corpus cannot validate. It is built and tested against purpose-made
fixtures, and that is stated rather than implied.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class Disposition(Enum):
    """What merging two runs' values for one property means."""

    #: Accumulate across runs. Both values are true; neither replaces the
    #: other. `pulse:owns`, `org:hasUnit`, `pulse:hasProfile`.
    UNION = "union"
    #: One value wins, chosen by `unify.merge`'s policy and recorded. Every
    #: property the shapes cap at 1, plus the uncapped ones that are
    #: semantically single — `schema:name`, `pulse:ror`.
    SELECT = "select"
    #: Never reaches the canonical graph. `pulse:partOfRun` is the substrate's
    #: provenance anchor: it differs per run *because* that is its job, and
    #: carrying it into canonical would assert that a canonical entity belongs
    #: to one arbitrary run's slice.
    PER_RUN = "per_run"
    #: Take the largest value. For a **cumulative** measure re-reported by the
    #: same source: `pulse:contributionCount` is GitHub's running total for a
    #: person on a repository, so two runs a week apart report 40 and then 43,
    #: not 40 and 3 more.
    #:
    #: Deliberately not `SUM`, which is the intuitive choice and is wrong:
    #: summing two observations of one platform's total double-counts every
    #: commit. Summing across *distinct platforms* would be defensible, but
    #: this layer merges a cluster without knowing which slice each value came
    #: from — so `MAX` under-counts a genuinely multi-platform contributor and
    #: never over-counts anyone, which is the safer error for a metric people
    #: read as "how much did they do".
    MAX = "max"
    #: Take the smallest. For a "first seen" date, where every re-observation
    #: can only push the true value earlier.
    MIN = "min"


#: Properties that belong to the substrate and never to canonical, whatever
#: shape declares them.
PER_RUN_PROPERTIES: frozenset[str] = frozenset({"pulse:partOfRun"})


#: Explicit disposition for every canonical property with **no** `sh:maxCount`.
#: Capped properties default to `SELECT` (a union would violate the shape), so
#: they are deliberately absent — this table is exactly the set the shapes
#: cannot classify.
UNCAPPED_DISPOSITIONS: dict[str, Disposition] = {
    # -- genuinely many: accumulate ------------------------------------------
    "org:hasMembership": Disposition.UNION,
    "org:hasUnit": Disposition.UNION,
    "org:unitOf": Disposition.UNION,
    "pulse:discipline": Disposition.UNION,
    "pulse:hasAuthoredArticle": Disposition.UNION,
    "pulse:hasContribution": Disposition.UNION,
    "pulse:hasDeposit": Disposition.UNION,
    "pulse:hasOrganizationProfile": Disposition.UNION,
    "pulse:hasProfile": Disposition.UNION,
    "pulse:owns": Disposition.UNION,
    "pulse:platformUsername": Disposition.UNION,
    "pulse:projectOutput": Disposition.UNION,
    "schema:author": Disposition.UNION,
    "schema:citation": Disposition.UNION,
    "schema:email": Disposition.UNION,
    "schema:funder": Disposition.UNION,
    "schema:member": Disposition.UNION,
    "schema:programmingLanguage": Disposition.UNION,
    "schema:sourceOrganization": Disposition.UNION,
    "schema:url": Disposition.UNION,
    # -- semantically one, loosely typed: choose ------------------------------
    #
    # Each of these is a list in the shapes and a single fact in the world.
    # `pulse:doi` and `pulse:orcidIdentifier` are the sharpest cases: an
    # article has one DOI and a person one ORCID, and unioning two would
    # produce an entity claiming two identities — which validates.
    "pulse:doi": Disposition.SELECT,
    "pulse:orcidIdentifier": Disposition.SELECT,
    "pulse:organizationType": Disposition.SELECT,
    "pulse:publicationType": Disposition.SELECT,
    "pulse:repositoryType": Disposition.SELECT,
    "pulse:ror": Disposition.SELECT,
    "schema:dateCreated": Disposition.SELECT,
    "schema:license": Disposition.SELECT,
    "schema:name": Disposition.SELECT,
    "time:hasBeginning": Disposition.SELECT,
    # -- aggregated, not chosen -----------------------------------------------
    #
    # The substrate now carries contributions (patch 06), so these are merged
    # rather than asserted, and merging them is neither a union nor a choice.
    # A count is a cumulative total and the dates are an observed range, so
    # the answer is an extremum — see `Disposition.MAX` for why it is not a
    # sum.
    "pulse:contributionCount": Disposition.MAX,
    "pulse:firstContributionDate": Disposition.MIN,
    "pulse:lastContributionDate": Disposition.MAX,
}


@dataclass(frozen=True, slots=True)
class TypeResolver:
    """The per-type half: how to recognise one entity and what to call it.

    `match_keys` is **ordered by precision**, and the order is load-bearing:
    clustering walks it and a match on an earlier key is trusted absolutely
    while a match on a later one may be overridden. ORCID before email before
    name is the difference between merging two researchers who share a name
    and merging two records of one researcher.

    `id_priority` is the promotion the architecture doc specifies — the same
    `ORCID → ROR → handle → urn:pulse:{uuid}` order `id_resolution.py` applies
    per request. Deliberately the same: extraction keeps its resolution (the
    decision recorded for phase 4), so the two agree by construction wherever
    they see the same evidence, and disagree only where the unifier saw more.
    """

    entity_type: str
    match_keys: tuple[str, ...]
    #: Property whose value becomes the canonical IRI, in promotion order.
    #: `None` entries mean "the entity's own id", used where identity is the
    #: id — repositories are `owner/name` and have nowhere to be promoted to.
    id_priority: tuple[str, ...] = ()
    #: The `owl:sameAs` subproperty that bridges a member IRI to the canonical
    #: one when they differ. `None` where the ontology declares none.
    same_as: str | None = None


#: One resolver per canonical type. The registry *is* the extension point:
#: adding a type is adding a row, and the type-agnostic machinery in
#: `unify.cluster` / `unify.merge` does not change.
RESOLVERS: dict[str, TypeResolver] = {
    "schema:Person": TypeResolver(
        entity_type="schema:Person",
        # ORCID is a global identifier and settles it. A shared platform
        # profile is next: it is what lets a person known by a GitHub handle in
        # one run and by an ORCID in another be recognised as one person —
        # which is the whole reason the profile model exists.
        match_keys=("pulse:orcidIdentifier", "pulse:hasProfile", "schema:email"),
        id_priority=("pulse:orcidIdentifier",),
        same_as="pulse:samePersonAs",
    ),
    "org:Organization": TypeResolver(
        entity_type="org:Organization",
        match_keys=("pulse:ror", "pulse:hasOrganizationProfile"),
        id_priority=("pulse:ror",),
        # From `ontology/patches/02-same-organization-as.patch`, which existed
        # for this and had no consumer until now.
        same_as="pulse:sameOrganizationAs",
    ),
    "schema:ScholarlyArticle": TypeResolver(
        entity_type="schema:ScholarlyArticle",
        match_keys=("pulse:doi", "pulse:hasDeposit"),
        id_priority=("pulse:doi",),
    ),
    "schema:SoftwareSourceCode": TypeResolver(
        entity_type="schema:SoftwareSourceCode",
        # Link, don't merge: a repository's identity *is* its platform handle,
        # there is no global id to promote to, and cross-platform relatedness
        # is modelled as `schema:Project` + `pulse:projectOutput` rather than
        # an identity merge. Clustering still runs, because two runs seeing the
        # same handle are the same repository and their properties accumulate.
        match_keys=("pulse:repositoryHandle",),
    ),
    # --- carried, not unified ------------------------------------------------
    #
    # A profile's IRI is already canonical by construction: `profile_iri` mints
    # it from (platform, handle, kind), so two runs seeing the same account
    # produce the same node and there is nothing to promote or alias. But they
    # still have to *reach* `graph:canonical`, because `PersonShape` constrains
    # `pulse:hasProfile` with `sh:class pulse:PlatformProfile` — skip them and
    # every person's profile edge dangles against a closed shape.
    #
    # Empty `match_keys` is what expresses "cluster by IRI only", so these need
    # no new machinery: the registry already means what they need.
    "pulse:PlatformProfile": TypeResolver(
        entity_type="pulse:PlatformProfile",
        match_keys=(),
    ),
    "pulse:OrganizationProfile": TypeResolver(
        entity_type="pulse:OrganizationProfile",
        match_keys=(),
    ),
    "pulse:Deposit": TypeResolver(
        entity_type="pulse:Deposit",
        match_keys=(),
    ),
    "pulse:Contribution": TypeResolver(
        entity_type="pulse:Contribution",
        # A composite `{person}__{repo}`, so identity follows the endpoints and
        # clustering by IRI is exactly right — two runs reporting the same pair
        # produce the same id. `remap` rebuilds it when an endpoint is renamed.
        match_keys=(),
    ),
    "org:Membership": TypeResolver(
        entity_type="org:Membership",
        # A composite `{person}__{org}`, so identity follows the endpoints.
        # Recomputed rather than matched once the endpoints canonicalise —
        # `unify.merge` rebuilds the id from the resolved pair.
        match_keys=(),
    ),
}


@dataclass(slots=True)
class MergePolicy:
    """Everything `unify.merge` needs, assembled once per run."""

    dispositions: dict[str, Disposition] = field(default_factory=dict)
    resolvers: dict[str, TypeResolver] = field(default_factory=dict)

    def disposition(self, prop: str, *, capped: bool = False) -> Disposition:
        """How to merge `prop`. `capped` is the shape's `sh:maxCount 1`.

        `capped` is not advisory. `sh:maxCount 1` and `UNION` cannot both hold:
        unioning two runs' values for a capped property writes a graph the
        closed canonical shapes reject, and it would do so silently, because
        the SHACL gate is warning-only. So a cap overrides the table and says
        so. `tests/v2/test_unify_policy.py` asserts the combination never
        occurs; this is the runtime floor under that test.
        """
        if prop in PER_RUN_PROPERTIES:
            return Disposition.PER_RUN
        explicit = self.dispositions.get(prop)
        if capped and explicit is Disposition.UNION:
            # Debug, not error: this fires on every `pulse:Contribution` in a
            # normal run, because `schema:author` is genuinely shape-dependent
            # — unbounded on articles and repositories, capped here — and the
            # table is per property. Expected behaviour, logged 46 times per
            # corpus pass at error level until this was measured.
            #
            # A table that marks an *always*-capped property UNION is a real
            # bug, and `test_no_always_capped_property_is_unioned` is the guard
            # for it. This branch cannot tell the two apart, so it stays quiet
            # and the test stays loud.
            logger.debug(
                "unify policy: %s is sh:maxCount 1 here but marked UNION; "
                "forcing SELECT to keep the canonical shape valid",
                prop,
            )
            return Disposition.SELECT
        if explicit is not None:
            return explicit
        # A capped property has no honest union, so SELECT is the only safe
        # default. An unknown *uncapped* one also lands here — the completeness
        # test exists so that is a test failure rather than a silent choice.
        return Disposition.SELECT


def single_valued_for(entity_type: str) -> frozenset[str]:
    """Properties the canonical shapes cap at 1 **for this type**.

    Read from the generated models rather than re-derived, and per type rather
    than globally, because exactly one canonical property is shape-dependent:
    `schema:author` is `sh:maxCount 1` on `pulse:Contribution` (a contribution
    has one author) and unbounded on `schema:ScholarlyArticle` and
    `schema:SoftwareSourceCode` (papers and repositories have many). A single
    global cap set gets one of those two wrong, and a per-property disposition
    table cannot express the difference at all — which is why this exists and
    why `UNCAPPED_DISPOSITIONS` marks `schema:author` as UNION and lets the
    cap override it where it applies.
    """
    from git_metadata_extractor.schema.generated.canonical import (  # noqa: PLC0415
        SINGLE_VALUED_BY_TARGET_CLASS,
    )

    return SINGLE_VALUED_BY_TARGET_CLASS.get(entity_type, frozenset())


def canonical_properties_for(entity_type: str) -> frozenset[str]:
    """Properties the **canonical** shape for this type declares.

    The unifier reads the substrate, where the entity shapes are open — so a
    source may legitimately assert something the closed canonical shape has no
    slot for. Copying it through publishes an invalid canonical graph.

    `schema:email` on a `schema:Person` is the case that found this: v3 puts a
    person's email on their `pulse:PlatformProfile`, not on the person, so
    `PersonShape` does not declare it and `sh:closed` rejects it. The raw
    projection happens to put emails on the profile already, which is why the
    120-repo corpus never triggered it — but the substrate is open by design
    and the next open-layer property to arrive would have.

    Read from the generated models rather than hand-listed, so a shape change
    moves it. Empty for a type with no canonical shape, which the caller must
    read as "no filter" rather than "drop everything".
    """
    from git_metadata_extractor.schema.generated.canonical import (  # noqa: PLC0415
        MODELS_BY_TARGET_CLASS,
    )

    model = MODELS_BY_TARGET_CLASS.get(entity_type)
    if model is None:
        return frozenset()
    return frozenset(
        field.alias
        for field in model.model_fields.values()
        if field.alias
    )


def default_policy() -> MergePolicy:
    return MergePolicy(
        dispositions=dict(UNCAPPED_DISPOSITIONS),
        resolvers=dict(RESOLVERS),
    )


__all__ = [
    "PER_RUN_PROPERTIES",
    "RESOLVERS",
    "UNCAPPED_DISPOSITIONS",
    "Disposition",
    "MergePolicy",
    "TypeResolver",
    "canonical_properties_for",
    "default_policy",
    "single_valued_for",
]
