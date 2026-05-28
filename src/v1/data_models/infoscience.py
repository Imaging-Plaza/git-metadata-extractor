"""
Infoscience data models for EPFL's Infoscience repository integration
"""

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class InfosciencePublication(BaseModel):
    """Publication metadata from Infoscience repository"""

    type: Literal["InfosciencePublication"] = Field(
        default="InfosciencePublication",
        description="Type discriminator for Infoscience entities",
    )
    uuid: Optional[str] = Field(
        description="DSpace UUID of the publication",
        default=None,
    )
    title: str = Field(description="Publication title")
    authors: List[str] = Field(
        description="List of author names",
        default_factory=list,
    )
    author_authorities: List[Optional[str]] = Field(
        description=(
            "Parallel array to `authors` (same length, same order) holding "
            "the DSpace `authority` UUID for each author when available "
            "(EPFL-affiliated, authority-controlled). `None` for non-EPFL "
            "authors that DSpace stores as bare names. Used by v2 to bind "
            "publication authors directly to Infoscience person entities "
            "without name-based fuzzy matching."
        ),
        default_factory=list,
    )
    abstract: Optional[str] = Field(
        description="Publication abstract or description",
        default=None,
    )
    doi: Optional[str] = Field(
        description="Digital Object Identifier",
        default=None,
    )
    publication_date: Optional[str] = Field(
        description="Publication date (YYYY-MM-DD or YYYY)",
        default=None,
    )
    publication_type: Optional[str] = Field(
        description="Type of publication (article, thesis, conference paper, etc.)",
        default=None,
    )
    url: Optional[HttpUrl] = Field(
        description="URL to the publication in Infoscience (format: https://infoscience.epfl.ch/entities/publication/{uuid})",
        default=None,
    )
    repository_url: Optional[HttpUrl] = Field(
        description="Code repository URL if available",
        default=None,
    )
    lab: Optional[str] = Field(
        description="Laboratory or research unit",
        default=None,
    )
    subjects: List[str] = Field(
        description="Subject keywords/tags",
        default_factory=list,
    )

    @field_validator("url", mode="before")
    @classmethod
    def validate_publication_url(cls, v):
        """Validate Infoscience publication URL format"""
        if v is None:
            return v
        if isinstance(v, str):
            pattern = r"^https://infoscience\.epfl\.ch/entities/publication/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
            if not re.match(pattern, v):
                raise ValueError(
                    f"Invalid Infoscience publication URL format: {v}. Expected: https://infoscience.epfl.ch/entities/publication/{{uuid}}",
                )
        return v

    def to_markdown(self) -> str:
        """Convert publication to markdown format"""
        md_parts = []

        # Title with link if available
        if self.url:
            md_parts.append(f"**[{self.title}]({self.url})**")
        else:
            md_parts.append(f"**{self.title}**")

        # UUID (important for creating relations)
        if self.uuid:
            md_parts.append(f"*UUID:* {self.uuid}")

        # URL (explicit field for LLM extraction)
        if self.url:
            md_parts.append(f"*URL:* {self.url}")

        # Authors
        if self.authors:
            authors_str = ", ".join(self.authors)
            md_parts.append(f"*Authors:* {authors_str}")

        # Publication info
        info_parts = []
        if self.publication_date:
            info_parts.append(f"Date: {self.publication_date}")
        if self.publication_type:
            info_parts.append(f"Type: {self.publication_type}")
        if self.doi:
            info_parts.append(f"DOI: {self.doi}")
        if info_parts:
            md_parts.append(" | ".join(info_parts))

        # Lab
        if self.lab:
            md_parts.append(f"*Lab:* {self.lab}")

        # Abstract
        if self.abstract:
            # Truncate long abstracts
            abstract_text = self.abstract[:300]
            if len(self.abstract) > 300:
                abstract_text += "..."
            md_parts.append(f"*Abstract:* {abstract_text}")

        # Repository URL
        if self.repository_url:
            md_parts.append(f"*Code Repository:* {self.repository_url}")

        # Subjects
        if self.subjects:
            subjects_str = ", ".join(self.subjects[:5])  # Limit to 5 subjects
            md_parts.append(f"*Subjects:* {subjects_str}")

        return "\n".join(md_parts)


class InfoscienceAuthor(BaseModel):
    """Author/researcher metadata from Infoscience"""

    type: Literal["InfoscienceAuthor"] = Field(
        default="InfoscienceAuthor",
        description="Type discriminator for Infoscience entities",
    )
    uuid: Optional[str] = Field(
        description="DSpace UUID of the author profile",
        default=None,
    )
    name: str = Field(description="Full name of the author")
    email: Optional[str] = Field(
        description="Email address",
        default=None,
    )
    orcid: Optional[str] = Field(
        description="ORCID identifier (format: 0000-0000-0000-0000 or https://orcid.org/0000-0000-0000-0000). Examples: '0000-0002-1234-5678', '0000-0000-0000-000X'",
        default=None,
    )
    affiliation: Optional[str] = Field(
        description="Primary affiliation (lab, department, etc.)",
        default=None,
    )
    profile_url: Optional[HttpUrl] = Field(
        description="URL to the author's Infoscience profile (format: https://infoscience.epfl.ch/entities/person/{uuid})",
        default=None,
    )

    @field_validator("orcid", mode="before")
    @classmethod
    def validate_orcid(cls, v):
        """Validate ORCID format and convert ID to URL if needed."""
        if v is None:
            return v

        if isinstance(v, str):
            # If it's already a URL, validate and return as-is (store as string)
            if v.startswith("http"):
                orcid_url_pattern = r"^https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"
                if not re.match(orcid_url_pattern, v):
                    raise ValueError(f"Invalid ORCID URL format: {v}")
                return v

            # If it's an ID, validate and return as-is (store as plain ID string)
            orcid_id_pattern = r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"
            if re.match(orcid_id_pattern, v):
                return v

            raise ValueError(
                f"Invalid ORCID format: {v}. Expected format: 0000-0000-0000-0000 or https://orcid.org/0000-0000-0000-0000",
            )

        return v

    @field_validator("profile_url", mode="before")
    @classmethod
    def validate_profile_url(cls, v):
        """Validate Infoscience person profile URL format"""
        if v is None:
            return v
        if isinstance(v, str):
            pattern = r"^https://infoscience\.epfl\.ch/entities/person/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
            if not re.match(pattern, v):
                raise ValueError(
                    f"Invalid Infoscience person profile URL format: {v}. Expected: https://infoscience.epfl.ch/entities/person/{{uuid}}",
                )
        return v

    def to_markdown(self) -> str:
        """Convert author to markdown format"""
        md_parts = []

        # Name with link if available
        if self.profile_url:
            md_parts.append(f"**[{self.name}]({self.profile_url})**")
        else:
            md_parts.append(f"**{self.name}**")

        # UUID (important for creating relations)
        if self.uuid:
            md_parts.append(f"*UUID:* {self.uuid}")

        # URL (explicit field for LLM extraction)
        if self.profile_url:
            md_parts.append(f"*URL:* {self.profile_url}")

        # Affiliation
        if self.affiliation:
            md_parts.append(f"*Affiliation:* {self.affiliation}")

        # ORCID
        if self.orcid:
            md_parts.append(f"*ORCID:* {self.orcid}")

        # Email
        if self.email:
            md_parts.append(f"*Email:* {self.email}")

        return "\n".join(md_parts)


class InfoscienceOrgUnit(BaseModel):
    """Organizational unit metadata from Infoscience"""

    type: Literal["InfoscienceOrgUnit"] = Field(
        default="InfoscienceOrgUnit",
        description="Type discriminator for Infoscience entities",
    )
    uuid: Optional[str] = Field(
        description="DSpace UUID of the organizational unit",
        default=None,
    )
    name: str = Field(description="Name of the lab or organizational unit")
    description: Optional[str] = Field(
        description="Description of the lab",
        default=None,
    )
    url: Optional[HttpUrl] = Field(
        description="URL to the lab's Infoscience page (format: https://infoscience.epfl.ch/entities/orgunit/{uuid})",
        default=None,
    )

    @field_validator("url", mode="before")
    @classmethod
    def validate_lab_url(cls, v):
        """Validate Infoscience orgunit URL format"""
        if v is None:
            return v
        if isinstance(v, str):
            pattern = r"^https://infoscience\.epfl\.ch/entities/orgunit/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
            if not re.match(pattern, v):
                raise ValueError(
                    f"Invalid Infoscience orgunit URL format: {v}. Expected: https://infoscience.epfl.ch/entities/orgunit/{{uuid}}",
                )
        return v

    parent_organization: Optional[str] = Field(
        description="Parent organization or department",
        default=None,
    )
    website: Optional[str] = Field(
        description="External website URL",
        default=None,
    )
    research_areas: List[str] = Field(
        description="Main research areas",
        default_factory=list,
    )

    def to_markdown(self) -> str:
        """Convert lab to markdown format"""
        md_parts = []

        # Name with link if available
        if self.url:
            md_parts.append(f"**[{self.name}]({self.url})**")
        else:
            md_parts.append(f"**{self.name}**")

        # UUID (important for creating relations)
        if self.uuid:
            md_parts.append(f"*UUID:* {self.uuid}")

        # URL (explicit field for LLM extraction)
        if self.url:
            md_parts.append(f"*URL:* {self.url}")

        # Parent organization
        if self.parent_organization:
            md_parts.append(f"*Part of:* {self.parent_organization}")

        # Description
        if self.description:
            desc_text = self.description[:200]
            if len(self.description) > 200:
                desc_text += "..."
            md_parts.append(f"*Description:* {desc_text}")

        # Website
        if self.website:
            md_parts.append(f"*Website:* {self.website}")

        # Research areas
        if self.research_areas:
            areas_str = ", ".join(self.research_areas[:5])
            md_parts.append(f"*Research Areas:* {areas_str}")

        return "\n".join(md_parts)


class InfoscienceSearchResult(BaseModel):
    """Wrapper for search results with pagination info"""

    total_results: int = Field(description="Total number of results found")
    page: int = Field(description="Current page number", default=1)
    results_per_page: int = Field(description="Number of results per page", default=10)
    publications: List[InfosciencePublication] = Field(
        description="List of publication results",
        default_factory=list,
    )
    authors: List[InfoscienceAuthor] = Field(
        description="List of author results",
        default_factory=list,
    )
    labs: List[InfoscienceOrgUnit] = Field(
        description="List of lab/organization results",
        default_factory=list,
    )

    def to_markdown(self) -> str:
        """Convert search results to markdown format"""
        md_parts = []

        # Header with counts
        if self.publications:
            total = self.total_results
            showing = len(self.publications)
            md_parts.append(
                f"## Publication Search Results ({showing} of {total} found)\n",
            )

            for idx, pub in enumerate(self.publications, 1):
                md_parts.append(f"### {idx}. {pub.title}")
                md_parts.append(pub.to_markdown())
                md_parts.append("")  # Empty line between results

        elif self.authors:
            total = self.total_results
            showing = len(self.authors)
            md_parts.append(f"## Author Search Results ({showing} of {total} found)\n")

            for idx, author in enumerate(self.authors, 1):
                md_parts.append(f"### {idx}. {author.name}")
                md_parts.append(author.to_markdown())
                md_parts.append("")

        elif self.labs:
            total = self.total_results
            showing = len(self.labs)
            md_parts.append(
                f"## Lab/Organization Search Results ({showing} of {total} found)\n",
            )

            for idx, lab in enumerate(self.labs, 1):
                md_parts.append(f"### {idx}. {lab.name}")
                md_parts.append(lab.to_markdown())
                md_parts.append("")

        else:
            md_parts.append("## ⚠️ STOP SEARCHING - No Results Found\n")
            md_parts.append(
                "**This search returned 0 results. The entity is NOT in Infoscience. Do NOT search again for this query because the results were 0.**",
            )

        # Footer
        if md_parts and self.total_results > 0:
            start = (self.page - 1) * self.results_per_page + 1
            end = min(self.page * self.results_per_page, self.total_results)
            md_parts.append(
                f"\n---\n*Showing results {start}-{end} of {self.total_results}*",
            )

        return "\n".join(md_parts)
