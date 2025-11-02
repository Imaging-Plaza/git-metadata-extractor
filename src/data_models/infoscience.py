"""
Infoscience data models for EPFL's Infoscience repository integration
"""

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field


class InfosciencePublication(BaseModel):
    """Publication metadata from Infoscience repository"""

    uuid: Optional[str] = Field(
        description="DSpace UUID of the publication",
        default=None,
    )
    title: str = Field(description="Publication title")
    authors: List[str] = Field(
        description="List of author names",
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
    url: Optional[str] = Field(
        description="URL to the publication in Infoscience",
        default=None,
    )
    repository_url: Optional[str] = Field(
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
        description="ORCID identifier",
        default=None,
    )
    affiliation: Optional[str] = Field(
        description="Primary affiliation (lab, department, etc.)",
        default=None,
    )
    profile_url: Optional[str] = Field(
        description="URL to the author's Infoscience profile",
        default=None,
    )
    publication_count: Optional[int] = Field(
        description="Number of publications in Infoscience",
        default=None,
    )
    research_interests: List[str] = Field(
        description="Research interests or topics",
        default_factory=list,
    )

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

        # Affiliation
        if self.affiliation:
            md_parts.append(f"*Affiliation:* {self.affiliation}")

        # ORCID
        if self.orcid:
            md_parts.append(f"*ORCID:* {self.orcid}")

        # Email
        if self.email:
            md_parts.append(f"*Email:* {self.email}")

        # Publication count
        if self.publication_count:
            md_parts.append(f"*Publications:* {self.publication_count}")

        # Research interests
        if self.research_interests:
            interests_str = ", ".join(self.research_interests[:5])
            md_parts.append(f"*Research Interests:* {interests_str}")

        return "\n".join(md_parts)


class InfoscienceLab(BaseModel):
    """Laboratory or organizational unit metadata from Infoscience"""

    uuid: Optional[str] = Field(
        description="DSpace UUID of the organizational unit",
        default=None,
    )
    name: str = Field(description="Name of the lab or organizational unit")
    description: Optional[str] = Field(
        description="Description of the lab",
        default=None,
    )
    url: Optional[str] = Field(
        description="URL to the lab's Infoscience page",
        default=None,
    )
    parent_organization: Optional[str] = Field(
        description="Parent organization or department",
        default=None,
    )
    website: Optional[str] = Field(
        description="External website URL",
        default=None,
    )
    publication_count: Optional[int] = Field(
        description="Number of publications from this lab",
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

        # Publication count
        if self.publication_count:
            md_parts.append(f"*Publications:* {self.publication_count}")

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
    labs: List[InfoscienceLab] = Field(
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
            md_parts.append(f"## Publication Search Results ({showing} of {total} found)\n")

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
            md_parts.append(f"## Lab/Organization Search Results ({showing} of {total} found)\n")

            for idx, lab in enumerate(self.labs, 1):
                md_parts.append(f"### {idx}. {lab.name}")
                md_parts.append(lab.to_markdown())
                md_parts.append("")

        else:
            md_parts.append("## No Results Found\n")
            md_parts.append("The search did not return any results.")

        # Footer
        if md_parts and self.total_results > 0:
            start = (self.page - 1) * self.results_per_page + 1
            end = min(self.page * self.results_per_page, self.total_results)
            md_parts.append(f"\n---\n*Showing results {start}-{end} of {self.total_results}*")

        return "\n".join(md_parts)

