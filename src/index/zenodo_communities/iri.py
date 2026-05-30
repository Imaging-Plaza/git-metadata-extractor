"""Canonical IRI scheme for `community_id` values.

Communities used to be keyed under a `<source>:<slug>` opaque prefix
(e.g. `zenodo:epfl`). That's namespaced and unique but it's not a
real identifier — downstream graph consumers can't dereference it,
and it's confusing next to the other catalogs which already use the
proper IRI form (`https://github.com/<org>`, `https://orcid.org/...`,
`https://ror.org/...`).

This module centralises the mapping `source × slug → canonical IRI`
so every ingest source emits the same shape, and existing rows can
be migrated by `bootstrap()` on next open.

Convention: no trailing slash, matches IRIs everywhere else in the
codebase.
"""

from __future__ import annotations

_IRI_TEMPLATES: dict[str, str] = {
    # https://zenodo.org/communities/<slug>
    "zenodo": "https://zenodo.org/communities/{slug}",
    # Reserved for future sources. Adding one here is the only change
    # needed to make `canonical_community_id(...)` recognise them.
    #
    #   "github":       "https://github.com/{slug}",
    #   "huggingface":  "https://huggingface.co/{slug}",
    #   "gitlab_epfl":  "https://gitlab.epfl.ch/{slug}",
}


class UnknownCommunitySource(ValueError):
    """Raised when no IRI template is registered for `source`."""


def canonical_community_id(source: str, slug: str) -> str:
    """Return the canonical IRI for a community.

    Raises:
        UnknownCommunitySource: when `source` has no registered template.
            Callers handling fresh ingest data should add the template
            above rather than swallow the exception.
    """
    template = _IRI_TEMPLATES.get(source)
    if template is None:
        msg = (
            f"No canonical IRI template registered for community source "
            f"{source!r}. Register one in src/index/zenodo_communities/iri.py."
        )
        raise UnknownCommunitySource(msg)
    return template.format(slug=slug)


__all__ = ["UnknownCommunitySource", "canonical_community_id"]
