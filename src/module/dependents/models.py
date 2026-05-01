"""Pydantic models for GitHub dependents data.

Internal data shape only — these are *not* part of the v2 ontology graph
output. They're the canonical structured form of "who depends on this
repo" for any consumer (CLI, future LLM tool, future pipeline stage).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DependentKind = Literal["REPOSITORY", "PACKAGE"]


class DependentItem(BaseModel):
    """One dependent listed on the GitHub dependents page."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(
        ...,
        description="GitHub `<owner>/<repo>` of the dependent.",
    )
    owner: str = Field(..., description="GitHub owner login of the dependent.")
    repo: str = Field(..., description="GitHub repository name of the dependent.")
    stars: int = Field(0, ge=0, description="Star count shown on the dependents row.")
    forks: int = Field(0, ge=0, description="Fork count shown on the dependents row.")


class ParsedDependentsPage(BaseModel):
    """Result of parsing one HTML dependents page (no aggregation)."""

    model_config = ConfigDict(extra="forbid")

    repository_count: int = Field(
        0,
        ge=0,
        description="Total `<N> Repositories` count from the toggle bar.",
    )
    package_count: int = Field(
        0,
        ge=0,
        description="Total `<N> Packages` count from the toggle bar.",
    )
    selected_kind: DependentKind | None = Field(
        None,
        description="Which view this page shows (REPOSITORY or PACKAGE), if detectable.",
    )
    items: list[DependentItem] = Field(
        default_factory=list,
        description="Dependent rows on this page only.",
    )
    next_cursor_url: str | None = Field(
        None,
        description="Absolute URL of the next page, or None if pagination is exhausted.",
    )


class DependentsResult(BaseModel):
    """Aggregated dependents lookup for one repository.

    Returned by `service.list_dependents`. Empty results (no dependents,
    page disabled, Selenium unavailable, etc.) are returned as a result
    with `available=false` and warnings populated — never as exceptions.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., description="The queried `<owner>/<repo>`.")
    kind: DependentKind = Field(..., description="Which view was queried.")
    total_count: int = Field(
        0,
        ge=0,
        description=(
            "Total `<N> Repositories|Packages` reported by GitHub for the "
            "selected view. May be much larger than `len(items)` if pagination "
            "was capped."
        ),
    )
    fetched_count: int = Field(
        0,
        ge=0,
        description="Number of dependents in `items`. <= total_count.",
    )
    truncated: bool = Field(
        False,
        description="True when `fetched_count < total_count` (pagination capped).",
    )
    items: list[DependentItem] = Field(
        default_factory=list,
        description=(
            "Dependents collected, in page order. Sort by stars descending in "
            "consumers if a 'top dependents' ranking is needed — we keep page "
            "order here to make the result deterministic and parser-checkable."
        ),
    )
    available: bool = Field(
        True,
        description=(
            "False when GitHub showed no dependents page (graph disabled, "
            "private repo without scope, Selenium unavailable, etc.)."
        ),
    )
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the result was produced (UTC).",
    )
    pages_fetched: int = Field(
        0,
        ge=0,
        description="Number of HTML pages parsed to produce this result.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Human-readable notes — e.g. degraded fetch, parse warnings.",
    )


__all__ = [
    "DependentItem",
    "DependentKind",
    "DependentsResult",
    "ParsedDependentsPage",
]
