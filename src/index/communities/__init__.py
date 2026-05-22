"""Communities index — cross-platform research-community registry.

Aggregates community/group/lab metadata from Zenodo (and, in future,
GitHub orgs, OpenAlex institutions, etc.) into a single DuckDB-backed
index that the v2 pipeline can use to anchor `org:Organization` /
`org:hasUnit` relationships when ORCID / ROR / Infoscience don't carry
the right identifier.

First iteration is Zenodo-only and covers EPFL, ETH Zürich, CERN, and
CERN openlab — the organisations whose research-software graphs we
currently care about most.
"""
