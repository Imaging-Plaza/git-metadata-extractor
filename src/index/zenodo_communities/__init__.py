"""Zenodo communities index — registry of Zenodo community pages.

Aggregates Zenodo community metadata (`https://zenodo.org/communities/
<slug>`) into a DuckDB-backed index that the v2 pipeline can use to
anchor `org:Organization` / `org:hasUnit` relationships when ORCID /
ROR / Infoscience don't carry the right identifier.

Scope (configured in `config/index/zenodo_communities.yaml`): EPFL,
ETH Zürich, CERN, and CERN openlab — the organisations whose
research-software graphs we currently care about most.

Naming: the module was previously called ``src.index.communities``
when we only had Zenodo as a source. The platform-prefixed name
matches the convention used by ``github_users``,
``github_organizations``, and ``huggingface_papers``; future
community sources from other platforms would each get their own
module (e.g. ``github_communities``, ``openalex_institutions``)
rather than sharing one generic ``communities`` namespace.
"""
