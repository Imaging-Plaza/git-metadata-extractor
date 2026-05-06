"""Pydantic schemas for the structured columns persisted into DuckDB.

Each row in `categories` carries the canonical ontology metadata, plus a
materialized embedding text built from the category name + Wikipedia
title + a handful of anchor concept names. The full payload returned by
graphai's `/ontology/tree/category/{id}` is stored in the `raw` column
so downstream consumers don't have to re-fetch.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CategoryRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category_id: str
    name: str | None = None
    depth: int | None = None
    parent_id: str | None = None
    wikipedia_page_id: str | None = None
    wikipedia_url: str | None = None
    graphsearch_url: str | None = None
    n_concepts: int = 0
    n_children: int = 0
    embedding_text: str | None = None


class CategoryConceptRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category_id: str
    concept_id: str
    concept_name: str | None = None
    rank: int = 0
