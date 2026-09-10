"""Generated from the Open Pulse SHACL shapes. Do not edit by hand.

    layer:  canonical
    source: ontology-shapes-canonical.ttl

Regenerate with:

    just ontology-prepare
    python scripts/v2/generate_from_ontology.py
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from git_metadata_extractor.schema.generated.enumerations import (
    AccessRight,
    Discipline,
    OrganizationType,
    Platform,
    PublicationType,
    RepositoryType,
)


class ArticleModel(BaseModel):
    """schema:ScholarlyArticle

    Closed shape: unknown properties are rejected.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    pulse_doi: list[
        Annotated[str, Field(pattern="^10\\.\\d{4,9}/[-._;()/:a-zA-Z0-9]+$")]
    ] = Field(..., alias="pulse:doi", description="Standard DOI validation.")
    pulse_hasDeposit: list[str] = Field(
        ...,
        alias="pulse:hasDeposit",
        description="Platform-specific deposits of this article (e.g. its Zenodo record, its Infoscience entry, its arXiv submission). Each carries that platform's own internal ID, access right, publisher and publication date.",
    )
    schema_author: list[str] = Field(
        ..., alias="schema:author", description="The author of this article"
    )
    schema_name: list[str] = Field(..., alias="schema:name")
    schema_sourceOrganization: list[str] | None = Field(
        None, alias="schema:sourceOrganization", description="Source Organization"
    )


class ContributionModel(BaseModel):
    """pulse:Contribution

    Closed shape: unknown properties are rejected.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    pulse_contributionCount: list[int] = Field(
        ..., alias="pulse:contributionCount", description="Number of commits made."
    )
    pulse_contributionTo: str = Field(
        ...,
        alias="pulse:contributionTo",
        description="The repository this contribution belongs to.",
    )
    pulse_firstContributionDate: list[datetime] | None = Field(
        None, alias="pulse:firstContributionDate", description="First Contribution Date"
    )
    pulse_gitAuthorEmail: str | None = Field(
        None,
        alias="pulse:gitAuthorEmail",
        description="The author email as it literally appeared in the aggregated commits.",
    )
    pulse_gitAuthorName: str | None = Field(
        None,
        alias="pulse:gitAuthorName",
        description="The author name as it literally appeared in the aggregated commits.",
    )
    pulse_lastContributionDate: list[datetime] | None = Field(
        None, alias="pulse:lastContributionDate", description="Last Contribution Date"
    )
    schema_author: str = Field(
        ...,
        alias="schema:author",
        description="The person who made these contributions.",
    )


class DepositModel(BaseModel):
    """pulse:Deposit

    Closed shape: unknown properties are rejected.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    pulse_accessRight: AccessRight | None = Field(
        None,
        alias="pulse:accessRight",
        description="Must be a valid IRI from the Access Right enumeration (e.g., pulse:OpenAccess).",
    )
    pulse_depositOf: str | None = Field(
        None,
        alias="pulse:depositOf",
        description="Back-link to the article this is a deposit of.",
    )
    pulse_platform: Platform = Field(
        ...,
        alias="pulse:platform",
        description="Which platform this deposit lives on (e.g. pulse:Zenodo, pulse:Infoscience, pulse:ArXiv).",
    )
    pulse_platformInternalId: str | None = Field(
        None,
        alias="pulse:platformInternalId",
        description="The stable platform-issued internal ID, interpreted relative to pulse:platform.",
    )
    pulse_publicationType: list[PublicationType] | None = Field(
        None,
        alias="pulse:publicationType",
        description="Must be a valid IRI from the Publication Type enumeration (e.g., pulse:JournalArticle).",
    )
    schema_datePublished: date = Field(
        ...,
        alias="schema:datePublished",
        description="The date this deposit was published/registered on its platform.",
    )
    schema_publisher: str | None = Field(
        None,
        alias="schema:publisher",
        description="The publisher's name, as free text.",
    )


class MembershipModel(BaseModel):
    """org:Membership

    Closed shape: unknown properties are rejected.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    org_organization: str = Field(
        ..., alias="org:organization", description="Organization"
    )
    org_role: str | None = Field(None, alias="org:role", description="Role")
    time_hasBeginning: list[date] | None = Field(
        None, alias="time:hasBeginning", description="Start Date"
    )
    time_hasEnd: date | None = Field(None, alias="time:hasEnd", description="End Date")


class OrganizationProfileModel(BaseModel):
    """pulse:OrganizationProfile

    Closed shape: unknown properties are rejected.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    pulse_organizationHandle: str = Field(
        ...,
        alias="pulse:organizationHandle",
        description="The org's handle on this platform (e.g. a GitHub login, a Hugging Face org slug, or an Infoscience organization UUID).",
    )
    pulse_organizationProfileOf: str | None = Field(
        None,
        alias="pulse:organizationProfileOf",
        description="Optional back-link to the organization this profile belongs to.",
    )
    pulse_platform: Platform = Field(
        ...,
        alias="pulse:platform",
        description="Which platform this profile lives on (from the Platform enumeration).",
    )
    pulse_platformInstance: str | None = Field(
        None,
        alias="pulse:platformInstance",
        description="Base URI of the self-hosted platform deployment this profile lives on; absent means the platform's canonical public instance.",
    )
    pulse_platformInternalId: str | None = Field(
        None,
        alias="pulse:platformInternalId",
        description="The stable platform-issued internal ID, interpreted relative to pulse:platform.",
    )
    schema_name: list[str] | None = Field(
        None,
        alias="schema:name",
        description="The name as it appears on this platform (may differ per platform).",
    )
    schema_url: list[str] | None = Field(
        None, alias="schema:url", description="Standard validation for any web URL."
    )


class OrganizationModel(BaseModel):
    """org:Organization

    Closed shape: unknown properties are rejected.

    Identity requires at least one of:
      - pulse:ror
      - pulse:hasOrganizationProfile
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    org_hasUnit: list[str] | None = Field(
        None,
        alias="org:hasUnit",
        description="Link to a subunit, project, or department.",
    )
    org_unitOf: list[str] | None = Field(
        None,
        alias="org:unitOf",
        description="Link back to the managing/parent organization.",
    )
    pulse_hasOrganizationProfile: list[str] | None = Field(
        None,
        alias="pulse:hasOrganizationProfile",
        description="Platform-scoped identities (GitHub, Hugging Face, Infoscience, ...) held by this organization. Each carries the handle/name/url used on that platform.",
    )
    pulse_organizationType: list[OrganizationType] | None = Field(
        None,
        alias="pulse:organizationType",
        description="Must be a valid IRI from the Organization Type enumeration (e.g., pulse:University).",
    )
    pulse_owns: list[str] | None = Field(
        None, alias="pulse:owns", description="Repositories owned by this organization."
    )
    pulse_ror: (
        list[Annotated[str, Field(pattern="^https://ror\\.org/[0-9a-z]{9}$")]] | None
    ) = Field(None, alias="pulse:ror", description="Standard ROR ID validation.")
    schema_name: list[str] = Field(..., alias="schema:name")

    @model_validator(mode="after")
    def _identity_present(self) -> OrganizationModel:
        """At least one identifying property must be set.

        Straight from the shape's `sh:or`. The hand-written JSON
        Schemas expressed this as an `anyOf` plus a prose description;
        here it fails at construction.
        """
        candidates = ("pulse_hasOrganizationProfile", "pulse_ror")
        if not any(getattr(self, name, None) for name in candidates):
            joined = ", ".join(candidates)
            message = "OrganizationModel requires at least one of: " + joined
            raise ValueError(message)
        return self


class PersonModel(BaseModel):
    """schema:Person

    Closed shape: unknown properties are rejected.

    Identity requires at least one of:
      - pulse:orcidIdentifier
      - pulse:hasProfile
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    org_hasMembership: list[str] | None = Field(
        None, alias="org:hasMembership", description="hasMembership"
    )
    pulse_hasAuthoredArticle: list[str] | None = Field(
        None,
        alias="pulse:hasAuthoredArticle",
        description="Scholarly articles authored by this person.",
    )
    pulse_hasContribution: list[str] | None = Field(
        None, alias="pulse:hasContribution", description="Has Contribution"
    )
    pulse_hasProfile: list[str] | None = Field(
        None,
        alias="pulse:hasProfile",
        description="Platform-scoped identities (GitHub, Infoscience, ...) held by this person. Each carries the name/email/handle used on that platform.",
    )
    pulse_orcidIdentifier: (
        list[Annotated[str, Field(pattern="^\\d{4}-\\d{4}-\\d{4}-\\d{3}[0-9X]$")]]
        | None
    ) = Field(
        None, alias="pulse:orcidIdentifier", description="ORCID identifier validation."
    )
    pulse_owns: list[str] | None = Field(
        None, alias="pulse:owns", description="Owns Repository"
    )
    schema_name: list[str] = Field(..., alias="schema:name")

    @model_validator(mode="after")
    def _identity_present(self) -> PersonModel:
        """At least one identifying property must be set.

        Straight from the shape's `sh:or`. The hand-written JSON
        Schemas expressed this as an `anyOf` plus a prose description;
        here it fails at construction.
        """
        candidates = ("pulse_hasProfile", "pulse_orcidIdentifier")
        if not any(getattr(self, name, None) for name in candidates):
            joined = ", ".join(candidates)
            message = "PersonModel requires at least one of: " + joined
            raise ValueError(message)
        return self


class PlatformProfileModel(BaseModel):
    """pulse:PlatformProfile

    Closed shape: unknown properties are rejected.

    Identity requires at least one of:
      - pulse:platformUsername
      - schema:email
      - schema:url
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    pulse_platform: Platform = Field(
        ...,
        alias="pulse:platform",
        description="Which platform this profile lives on (from the Platform enumeration).",
    )
    pulse_platformInstance: str | None = Field(
        None,
        alias="pulse:platformInstance",
        description="Base URI of the self-hosted platform deployment this profile lives on; absent means the platform's canonical public instance.",
    )
    pulse_platformInternalId: str | None = Field(
        None,
        alias="pulse:platformInternalId",
        description="The stable platform-issued internal ID, interpreted relative to pulse:platform.",
    )
    pulse_platformNodeId: str | None = Field(
        None,
        alias="pulse:platformNodeId",
        description="A platform's own opaque global node ID, interpreted relative to pulse:platform.",
    )
    pulse_platformUsername: list[str] | None = Field(
        None,
        alias="pulse:platformUsername",
        description="The person's handle on this platform (e.g. a GitHub login, Hugging Face username, or Infoscience person identifier), interpreted relative to pulse:platform.",
    )
    pulse_profileOf: str | None = Field(
        None,
        alias="pulse:profileOf",
        description="Optional back-link to the person this profile belongs to.",
    )
    schema_email: (
        list[Annotated[str, Field(pattern="^[\\w\\-\\.\\+]+@([\\w-]+\\.)+[\\w-]{2,}$")]]
        | None
    ) = Field(
        None,
        alias="schema:email",
        description="Platform email pattern allowing '+' sub-addressing (e.g. GitHub '1024+user@users.noreply.github.com').",
    )
    schema_name: list[str] | None = Field(
        None,
        alias="schema:name",
        description="The name as it appears on this platform (may differ per platform).",
    )
    schema_url: list[str] | None = Field(
        None, alias="schema:url", description="Standard validation for any web URL."
    )

    @model_validator(mode="after")
    def _identity_present(self) -> PlatformProfileModel:
        """At least one identifying property must be set.

        Straight from the shape's `sh:or`. The hand-written JSON
        Schemas expressed this as an `anyOf` plus a prose description;
        here it fails at construction.
        """
        candidates = ("pulse_platformUsername", "schema_email", "schema_url")
        if not any(getattr(self, name, None) for name in candidates):
            joined = ", ".join(candidates)
            message = "PlatformProfileModel requires at least one of: " + joined
            raise ValueError(message)
        return self


class ProjectModel(BaseModel):
    """schema:Project

    Closed shape: unknown properties are rejected.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    pulse_projectOutput: list[str] | None = Field(
        None,
        alias="pulse:projectOutput",
        description="A repository or scholarly article produced by this project.",
    )
    schema_description: str | None = Field(
        None,
        alias="schema:description",
        description="A short free-text description of the item.",
    )
    schema_funder: list[str] | None = Field(
        None,
        alias="schema:funder",
        description="The organization(s) funding this project.",
    )
    schema_member: list[str] | None = Field(
        None,
        alias="schema:member",
        description="A person participating in this project.",
    )
    schema_name: list[str] = Field(..., alias="schema:name")
    schema_sourceOrganization: list[str] | None = Field(
        None,
        alias="schema:sourceOrganization",
        description="The organization(s) hosting or running this project.",
    )
    schema_url: list[str] | None = Field(
        None, alias="schema:url", description="Standard validation for any web URL."
    )
    time_hasBeginning: list[date] | None = Field(
        None, alias="time:hasBeginning", description="Start Date"
    )
    time_hasEnd: date | None = Field(None, alias="time:hasEnd", description="End Date")


class RepositoryModel(BaseModel):
    """schema:SoftwareSourceCode

    Closed shape: unknown properties are rejected.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    pulse_discipline: list[Discipline] | None = Field(
        None,
        alias="pulse:discipline",
        description="Must be a valid IRI from the Wikidata-based Discipline enumeration.",
    )
    pulse_isForkOf: str | None = Field(
        None, alias="pulse:isForkOf", description="The repository this was forked from."
    )
    pulse_ownedBy: str | None = Field(
        None,
        alias="pulse:ownedBy",
        description="The Person or Organization that owns this repository.",
    )
    pulse_platform: Platform = Field(
        ...,
        alias="pulse:platform",
        description="The code-hosting platform this repository lives on (e.g. pulse:GitHub, pulse:GitLab, pulse:Bitbucket).",
    )
    pulse_platformInternalId: str | None = Field(
        None,
        alias="pulse:platformInternalId",
        description="The stable platform-issued internal ID, interpreted relative to pulse:platform.",
    )
    pulse_platformNodeId: str | None = Field(
        None,
        alias="pulse:platformNodeId",
        description="A platform's own opaque global node ID, interpreted relative to pulse:platform.",
    )
    pulse_repositoryForks: int | None = Field(
        None,
        alias="pulse:repositoryForks",
        description="Number of forks for the repository.",
    )
    pulse_repositoryHandle: str = Field(
        ...,
        alias="pulse:repositoryHandle",
        pattern="^[a-zA-Z0-9\\-_.]+(/[a-zA-Z0-9\\-_.]+)+$",
        description="The repository handle, e.g. 'owner/repository' (GitHub, Bitbucket) or 'group/subgroup/project' (GitLab's nested groups).",
    )
    pulse_repositoryStars: int | None = Field(
        None,
        alias="pulse:repositoryStars",
        description="Number of stars for the repository.",
    )
    pulse_repositoryType: list[RepositoryType] | None = Field(
        None,
        alias="pulse:repositoryType",
        description="Must be a valid IRI from the Repository Type enumeration (e.g., pulse:Software).",
    )
    schema_author: list[str] = Field(
        ..., alias="schema:author", description="The author of this repository."
    )
    schema_citation: list[str] | None = Field(
        None, alias="schema:citation", description="Citation"
    )
    schema_dateCreated: list[datetime] | None = Field(
        None, alias="schema:dateCreated", description="Creation Date"
    )
    schema_license: list[str] | None = Field(
        None, alias="schema:license", description="License"
    )
    schema_name: list[str] = Field(..., alias="schema:name")
    schema_programmingLanguage: list[str] | None = Field(
        None, alias="schema:programmingLanguage"
    )


#: `sh:targetClass` -> the model generated for its shape.
MODELS_BY_TARGET_CLASS: dict[str, type[BaseModel]] = {
    "schema:ScholarlyArticle": ArticleModel,
    "pulse:Contribution": ContributionModel,
    "pulse:Deposit": DepositModel,
    "org:Membership": MembershipModel,
    "pulse:OrganizationProfile": OrganizationProfileModel,
    "org:Organization": OrganizationModel,
    "schema:Person": PersonModel,
    "pulse:PlatformProfile": PlatformProfileModel,
    "schema:Project": ProjectModel,
    "schema:SoftwareSourceCode": RepositoryModel,
}


#: Properties this layer gives `sh:maxCount 1`, per target class. Read off
#: the shapes at generation time so no consumer has to re-derive it.
SINGLE_VALUED_BY_TARGET_CLASS: dict[str, frozenset[str]] = {
    "schema:ScholarlyArticle": frozenset({}),
    "pulse:Contribution": frozenset(
        {
            "pulse:contributionTo",
            "pulse:gitAuthorEmail",
            "pulse:gitAuthorName",
            "schema:author",
        }
    ),
    "pulse:Deposit": frozenset(
        {
            "pulse:accessRight",
            "pulse:depositOf",
            "pulse:platform",
            "pulse:platformInternalId",
            "schema:datePublished",
            "schema:publisher",
        }
    ),
    "org:Membership": frozenset({"org:organization", "org:role", "time:hasEnd"}),
    "pulse:OrganizationProfile": frozenset(
        {
            "pulse:organizationHandle",
            "pulse:organizationProfileOf",
            "pulse:platform",
            "pulse:platformInstance",
            "pulse:platformInternalId",
        }
    ),
    "org:Organization": frozenset({}),
    "schema:Person": frozenset({}),
    "pulse:PlatformProfile": frozenset(
        {
            "pulse:platform",
            "pulse:platformInstance",
            "pulse:platformInternalId",
            "pulse:platformNodeId",
            "pulse:profileOf",
        }
    ),
    "schema:Project": frozenset({"schema:description", "time:hasEnd"}),
    "schema:SoftwareSourceCode": frozenset(
        {
            "pulse:isForkOf",
            "pulse:ownedBy",
            "pulse:platform",
            "pulse:platformInternalId",
            "pulse:platformNodeId",
            "pulse:repositoryForks",
            "pulse:repositoryHandle",
            "pulse:repositoryStars",
        }
    ),
}
