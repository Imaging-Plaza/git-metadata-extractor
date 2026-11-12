"""The write side: the quad store this service accumulates the substrate in.

`providers/` is the read layer — GitHub, ROR, ORCID, the RAG indexes. This is
its counterpart, and the distinction is worth keeping in the package layout:
everything under `providers/` answers "what does this source say?", and
everything here answers "what have we recorded?".
"""

from git_metadata_extractor.store.oxigraph import (
    OxigraphStore,
    StoreUnavailableError,
    StoreWriteError,
)
from git_metadata_extractor.store.terms import (
    UnusableIRIError,
    iri_term,
    literal_term,
)

__all__ = [
    "OxigraphStore",
    "StoreUnavailableError",
    "StoreWriteError",
    "UnusableIRIError",
    "iri_term",
    "literal_term",
]
