"""Bio resolver — LLM-driven affiliation resolution for persons whose
`_company` was empty and whose bio / orcid / readme text the
deterministic stage could not parse."""

from git_metadata_extractor.agents.llm.refiners.bio_resolver.agent import (
    CONFIDENCE_FLOOR,
    PROFILE_README_CAP_CHARS,
    BioResolverAgent,
    BioResolverInput,
    BioResolverPatch,
    UnresolvedPerson,
)

__all__ = [
    "CONFIDENCE_FLOOR",
    "PROFILE_README_CAP_CHARS",
    "BioResolverAgent",
    "BioResolverInput",
    "BioResolverPatch",
    "UnresolvedPerson",
]
