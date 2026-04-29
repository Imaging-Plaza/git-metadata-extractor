"""
Atomic agents for repository analysis pipeline.

This module implements multiple two-stage agent pipelines:

Main Analysis Pipeline:
1. Context compiler: Gathers repository information using tools
2. Structured output: Produces structured metadata from compiled context
3. Repository classifier: Classifies repository type and discipline
4. Organization identifier: Identifies related organizations and relationships

Post-Enrichment Pipelines:
5. Linked entities searcher: Searches academic catalogs with tools → structures results
6. EPFL final checker: Compiles enriched data → assesses EPFL relationship
"""

from .context_compiler import compile_repository_context
from .epfl_final_checker import (
    assess_final_epfl_relationship,
    compile_enriched_data_for_epfl,
)
from .linked_entities_searcher import (
    search_academic_catalogs,
    structure_linked_entities,
)
from .organization_identifier import identify_related_organizations
from .repository_classifier import classify_repository_type_and_discipline
from .structured_output import generate_structured_output

__all__ = [
    "compile_repository_context",
    "generate_structured_output",
    "classify_repository_type_and_discipline",
    "identify_related_organizations",
    "compile_enriched_data_for_epfl",
    "assess_final_epfl_relationship",
    "search_academic_catalogs",
    "structure_linked_entities",
]
