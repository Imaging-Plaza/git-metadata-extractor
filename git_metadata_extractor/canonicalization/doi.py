"""V2 canonicalization re-export of the shared DOI helper.

The actual implementation lives in the ``open_pulse_sources`` library at
``open_pulse_sources.index._shared.doi`` (used by every catalog backend).
This thin re-export keeps v2 extraction code from reaching across the
package boundary into the index subsystem — `from git_metadata_extractor.canonicalization
import doi_iri, parse_doi` is the canonical import for any v2 site.
"""

from __future__ import annotations

from open_pulse_sources.index._shared.doi import doi_iri, parse_doi

__all__ = ["doi_iri", "parse_doi"]
