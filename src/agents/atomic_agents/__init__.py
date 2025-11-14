"""
Atomic agents for repository analysis pipeline.

This module implements a two-stage agent pipeline:
1. Context compiler: Gathers repository information using tools
2. Structured output: Produces structured metadata from compiled context
3. EPFL checker: Assesses EPFL relationship from compiled context
"""

from .context_compiler import compile_repository_context
from .epfl_checker import check_epfl_relationship
from .structured_output import generate_structured_output

__all__ = [
    "compile_repository_context",
    "generate_structured_output",
    "check_epfl_relationship",
]
