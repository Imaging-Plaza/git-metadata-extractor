"""
Academic Catalog Data Models

Unified models for academic catalog relationships across multiple catalogs
(Infoscience, OpenAlex, EPFL Graph, etc.)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, Field

from .infoscience import InfoscienceAuthor, InfoscienceLab, InfosciencePublication


class CatalogType(str, Enum):
    """Supported academic catalog types"""

    INFOSCIENCE = "infoscience"
    OPENALEX = "openalex"
    EPFL_GRAPH = "epfl_graph"


class EntityType(str, Enum):
    """Types of entities in academic catalogs"""

    PUBLICATION = "publication"
    PERSON = "person"
    ORGUNIT = "orgunit"


class linkedEntitiesRelation(BaseModel):
    """
    Relationship to an entity in an academic catalog.

    This model supports multiple academic catalogs (Infoscience, OpenAlex, EPFL Graph)
    and multiple entity types (publications, persons, organizational units).

    The entity field contains the full entity details embedded within the relation.
    """

    catalogType: CatalogType = Field(
        description="Which academic catalog this entity comes from",
    )

    entityType: EntityType = Field(
        description="Type of entity (publication, person, orgunit)",
    )

    entity: Union[
        InfosciencePublication,
        InfoscienceAuthor,
        InfoscienceLab,
        dict[str, Any],
    ] = Field(
        description="Full entity details. For Infoscience: InfosciencePublication, "
        "InfoscienceAuthor, or InfoscienceLab. For other catalogs: structured dict.",
    )

    confidence: float = Field(
        description="Confidence score (0.0-1.0) for this relationship",
        ge=0.0,
        le=1.0,
        default=0.8,
    )

    justification: str = Field(
        description="Explanation of why this entity is related and how it was found",
    )

    matchedOn: Optional[list[str]] = Field(
        description="Fields used to match this entity (e.g., ['name', 'email'], ['doi'])",
        default_factory=list,
    )

    def get_display_name(self) -> str:
        """Get a display name for this entity."""
        if isinstance(
            self.entity,
            (InfosciencePublication, InfoscienceLab, InfoscienceAuthor),
        ):
            return getattr(self.entity, "title", None) or getattr(
                self.entity,
                "name",
                "Unknown",
            )
        if isinstance(self.entity, dict):
            return self.entity.get("title") or self.entity.get("name", "Unknown")
        return "Unknown"

    def get_url(self) -> Optional[str]:
        """Get the URL for this entity if available."""
        if isinstance(self.entity, InfosciencePublication):
            return self.entity.url
        if isinstance(self.entity, InfoscienceAuthor):
            return self.entity.profile_url
        if isinstance(self.entity, InfoscienceLab):
            return self.entity.url
        if isinstance(self.entity, dict):
            return self.entity.get("url") or self.entity.get("profile_url")
        return None

    def to_markdown(self) -> str:
        """Convert relation to markdown format for logging/display."""
        lines = []
        lines.append(f"**{self.catalogType.value}** - {self.entityType.value}")
        lines.append(f"*Entity:* {self.get_display_name()}")

        url = self.get_url()
        if url:
            lines.append(f"*URL:* {url}")

        lines.append(f"*Confidence:* {self.confidence:.2f}")
        lines.append(f"*Justification:* {self.justification}")

        if self.matchedOn:
            lines.append(f"*Matched on:* {', '.join(self.matchedOn)}")

        return "\n".join(lines)


class linkedEntitiesEnrichmentResult(BaseModel):
    """
    Result from academic catalog enrichment agent.

    Contains organized academic catalog relations by what was searched for:
    - repository_relations: Publications about the repository/project itself
    - author_relations: Relations for each author (person profiles, their publications)
    - organization_relations: Relations for each organization (orgunit profiles, publications)
    """

    repository_relations: list[linkedEntitiesRelation] = Field(
        description="Relations found for the repository itself (publications about the repository name/project)",
        default_factory=list,
    )

    author_relations: dict[str, list[linkedEntitiesRelation]] = Field(
        description="Relations found for each author, keyed by author name as provided",
        default_factory=dict,
    )

    organization_relations: dict[str, list[linkedEntitiesRelation]] = Field(
        description="Relations found for each organization, keyed by organization name as provided",
        default_factory=dict,
    )

    searchStrategy: Optional[str] = Field(
        description="Description of the search strategy used",
        default=None,
    )

    catalogsSearched: list[CatalogType] = Field(
        description="Which catalogs were searched",
        default_factory=list,
    )

    totalSearches: int = Field(
        description="Total number of searches performed",
        default=0,
    )

    # Token usage tracking (populated by agent)
    inputTokens: Optional[int] = Field(
        description="Input tokens used by the enrichment agent",
        default=None,
    )

    outputTokens: Optional[int] = Field(
        description="Output tokens used by the enrichment agent",
        default=None,
    )

    # Backward compatibility - aggregates all relations
    @property
    def relations(self) -> list[linkedEntitiesRelation]:
        """Get all relations combined (for backward compatibility)."""
        all_relations = list(self.repository_relations)
        for author_rels in self.author_relations.values():
            all_relations.extend(author_rels)
        for org_rels in self.organization_relations.values():
            all_relations.extend(org_rels)
        return all_relations

    def get_by_catalog(
        self,
        catalog_type: CatalogType,
    ) -> list[linkedEntitiesRelation]:
        """Get relations from a specific catalog."""
        return [r for r in self.relations if r.catalogType == catalog_type]

    def get_by_entity_type(
        self,
        entity_type: EntityType,
    ) -> list[linkedEntitiesRelation]:
        """Get relations of a specific entity type."""
        return [r for r in self.relations if r.entityType == entity_type]

    def get_publications(self) -> list[linkedEntitiesRelation]:
        """Get all publication relations."""
        return self.get_by_entity_type(EntityType.PUBLICATION)

    def get_persons(self) -> list[linkedEntitiesRelation]:
        """Get all person relations."""
        return self.get_by_entity_type(EntityType.PERSON)

    def get_orgunits(self) -> list[linkedEntitiesRelation]:
        """Get all organizational unit relations."""
        return self.get_by_entity_type(EntityType.ORGUNIT)

    def to_markdown(self) -> str:
        """Convert enrichment result to markdown format."""
        lines = []
        lines.append("## Academic Catalog Enrichment Results\n")

        if self.searchStrategy:
            lines.append(f"**Search Strategy:** {self.searchStrategy}\n")

        lines.append(
            f"**Catalogs Searched:** {', '.join([c.value for c in self.catalogsSearched])}",
        )
        lines.append(f"**Total Searches:** {self.totalSearches}")
        lines.append(f"**Relations Found:** {len(self.relations)}\n")

        if self.relations:
            lines.append("### Relations\n")
            for idx, relation in enumerate(self.relations, 1):
                lines.append(f"#### {idx}. {relation.get_display_name()}")
                lines.append(relation.to_markdown())
                lines.append("")  # Empty line between relations
        else:
            lines.append("*No relations found*")

        return "\n".join(lines)
