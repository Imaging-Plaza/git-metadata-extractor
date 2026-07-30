"""V2 canonicalization re-export of the shared DOI helper.

The actual implementation lives at ``src/index/_shared/doi.py`` (used
by every catalog backend since the `feat/all-catalogs-doi-urls` PR).
This thin re-export keeps v2 extraction code from reaching across the
package boundary into the index subsystem — `from git_metadata_extractor.canonicalization
import doi_iri, parse_doi` is the canonical import for any v2 site.
"""

from __future__ import annotations

from open_pulse_sources.index._shared.doi import doi_iri, parse_doi

__all__ = ["doi_iri", "parse_doi"]
