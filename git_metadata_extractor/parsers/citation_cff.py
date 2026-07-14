"""Parser for ``CITATION.cff`` (Citation File Format).

The Citation File Format (`https://citation-file-format.github.io/`)
is the community standard for declaring how to cite software.
v1.2.0 is the current schema. Every well-cited research-software
repo on GitHub ships a CITATION.cff at root.

The format carries strong identity signal the GME pipeline would
otherwise have to extract from README prose:

  - canonical project title, version, release date, license;
  - software DOI(s) + URL identifiers + Software Heritage ID;
  - author list with full names, ORCIDs, emails, affiliations
    (PII-relevant — `_anonymize_email` should still be applied
    downstream if needed);
  - the `preferred-citation` block — a full bibliographic
    reference for the paper that introduces the software,
    typically with its own DOI distinct from the software DOI;
  - cross-references in `references[]`.

This parser is **lossy by design**: malformed sub-trees are
silently dropped rather than failing the whole parse. Callers
get either a populated dict or ``None``. The shape mirrors
``git_metadata_extractor/parsers/publiccode.py`` so the two parsers are
behaviour-twins from the caller's perspective.

Caller-side enrichment (lifting CITATION.cff fields onto the
Repository's `schema:*` / `pulse:*` predicates) is intentionally
NOT done here — that's a separate concern with policy questions
(does CITATION.cff trump what an agent already wrote? etc.) and
belongs in a future pipeline stage. This parser is pure data.

Spec references:
  - https://github.com/citation-file-format/citation-file-format/blob/main/schema-guide.md
  - https://citation-file-format.github.io/
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

import yaml

from open_pulse_sources.index._shared.doi import doi_iri

logger = logging.getLogger(__name__)


# Top-level fields that are plain scalars in v1.2.0.
_TOP_LEVEL_SCALAR_FIELDS: tuple[str, ...] = (
    "cff-version",
    "message",
    "title",
    "abstract",
    "version",
    "date-released",
    "commit",
    "doi",                 # deprecated but still in the wild — promoted into `identifiers`
    "license",
    "license-url",
    "repository",
    "repository-artifact",
    "repository-code",
    "type",                # "software" | "dataset" — defaults to software
    "url",
)

# Top-level list-of-strings fields.
_TOP_LEVEL_LIST_FIELDS: tuple[str, ...] = (
    "keywords",
)


# Person fields (author / contact / editor / translator / sender / recipient).
_PERSON_FIELDS: tuple[str, ...] = (
    "given-names", "family-names", "name-particle", "name-suffix",
    "name-suffix", "email", "affiliation", "orcid", "website",
    "address", "city", "country", "region", "post-code", "tel", "fax",
    "alias",
)

# Entity (group / organization) fields. The distinguishing field is `name`.
_ENTITY_FIELDS: tuple[str, ...] = (
    "name", "email", "address", "city", "country", "region",
    "post-code", "tel", "fax", "location", "date-start", "date-end",
    "website", "alias",
)

# Reference object (preferred-citation, references[]) — subset of the
# spec's enormous field list. We capture the citation-critical ones.
_REFERENCE_SCALAR_FIELDS: tuple[str, ...] = (
    "type", "title", "abstract", "doi", "url", "year", "month",
    "start", "end", "pages", "journal", "volume", "issue", "publisher",
    "publisher-name", "edition", "isbn", "issn", "pmcid", "nihmsid",
    "license", "license-url", "version", "commit",
    "date-accessed", "date-downloaded", "date-released", "date-published",
    "languages", "medium", "thesis-type", "status", "scope", "format",
    "institution", "conference", "data-type", "database",
    "database-provider", "department", "entry", "loc-start", "loc-end",
    "number", "section", "term",
)
_REFERENCE_LIST_FIELDS: tuple[str, ...] = (
    "keywords", "filename",
)
_REFERENCE_PERSON_FIELDS: tuple[str, ...] = (
    "authors", "editors", "editors-series", "translators",
    "senders", "recipients",
)


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _coerce_str(value: Any) -> str | None:
    """Convert YAML scalar to a clean string, handling date/datetime
    objects that PyYAML decodes natively from `YYYY-MM-DD` literals."""
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return None


def _coerce_str_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else None
    if isinstance(value, list):
        out = []
        for item in value:
            s = _coerce_str(item)
            if s:
                out.append(s)
        return out or None
    return None


def _coerce_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


# ---------------------------------------------------------------------------
# Person / Entity parsing
# ---------------------------------------------------------------------------


def _parse_person_or_entity(value: Any) -> dict[str, Any] | None:
    """Authors / contacts / editors are either Person dicts (with
    `given-names`/`family-names`) or Entity dicts (with `name`).
    Spec says they're either-or per item; we honour that — items
    that have neither shape get dropped."""
    if not isinstance(value, dict):
        return None
    has_person_name = any(
        _coerce_str(value.get(k)) for k in ("given-names", "family-names")
    )
    has_entity_name = bool(_coerce_str(value.get("name")))
    if not has_person_name and not has_entity_name:
        return None

    out: dict[str, Any] = {}
    fields = _PERSON_FIELDS if has_person_name else _ENTITY_FIELDS
    for k in fields:
        coerced = _coerce_str(value.get(k))
        if coerced:
            out[k] = coerced
    # Tag the kind so downstream consumers don't have to re-derive it.
    out["__kind__"] = "person" if has_person_name else "entity"
    return out


def _parse_person_or_entity_list(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        parsed = _parse_person_or_entity(item)
        if parsed:
            out.append(parsed)
    return out or None


# ---------------------------------------------------------------------------
# Reference parsing (preferred-citation, references[])
# ---------------------------------------------------------------------------


def _parse_reference(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for k in _REFERENCE_SCALAR_FIELDS:
        coerced = _coerce_str(value.get(k))
        if coerced:
            out[k] = coerced
    # DOI fields land as bare strings in the YAML wire format
    # ('10.1234/example') but the rest of the project standardises on
    # canonical `https://doi.org/<bare>` URL form — `src/index/_shared/
    # doi.py::doi_iri` is the shared promotion helper, used by every
    # index catalog. Apply it here too so downstream consumers see the
    # same shape regardless of source.
    if "doi" in out:
        promoted = doi_iri(out["doi"])
        if promoted:
            out["doi"] = promoted
    for k in _REFERENCE_LIST_FIELDS:
        coerced_list = _coerce_str_list(value.get(k))
        if coerced_list:
            out[k] = coerced_list
    for k in _REFERENCE_PERSON_FIELDS:
        coerced_persons = _parse_person_or_entity_list(value.get(k))
        if coerced_persons:
            out[k] = coerced_persons
    return out or None


def _parse_references(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        parsed = _parse_reference(item)
        if parsed:
            out.append(parsed)
    return out or None


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


def _parse_identifiers(value: Any) -> list[dict[str, Any]] | None:
    """``identifiers`` is a list of ``{type, value, description?}``
    where ``type`` is one of doi / url / swh / other.

    DOI entries are canonicalised to `https://doi.org/<bare>` via the
    shared `open_pulse_sources.index._shared.doi.doi_iri` helper so they match the
    URL form every other catalog uses.
    """
    if not isinstance(value, list):
        return None
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        t = _coerce_str(item.get("type"))
        v = _coerce_str(item.get("value"))
        if not t or not v:
            continue
        kind = t.lower()
        if kind == "doi":
            promoted = doi_iri(v)
            if promoted:
                v = promoted
        entry: dict[str, Any] = {"type": kind, "value": v}
        desc = _coerce_str(item.get("description"))
        if desc:
            entry["description"] = desc
        out.append(entry)
    return out or None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_citation_cff(content: str | None) -> dict[str, Any] | None:
    """Parse a ``CITATION.cff`` document.

    Returns a dict whose keys are the CFF v1.2.0 field names
    (kebab-case, per spec) for fields that were present and
    well-typed in the input. Always lossy by design — malformed
    sub-trees are silently dropped rather than failing the whole
    parse.

    Returns ``None`` when:
      - ``content`` is None / empty / whitespace,
      - the YAML payload doesn't load to a mapping,
      - the YAML is malformed.

    Special handling:
      - The deprecated top-level ``doi`` field is preserved
        verbatim but ALSO promoted into ``identifiers`` as a
        synthesised ``{type: "doi", value: <doi>}`` entry, so
        downstream consumers can iterate ``identifiers`` uniformly
        regardless of CFF spec age.
      - Dates are coerced from native ``date`` / ``datetime`` objects
        that PyYAML decodes from `YYYY-MM-DD` literals back into
        ISO-8601 strings.
      - Author / contact / preferred-citation.authors items carry
        an internal ``__kind__`` tag (``"person"`` | ``"entity"``)
        so callers don't have to re-derive the Person/Entity
        distinction from field presence.
    """
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError:
        logger.warning("CITATION.cff: YAML parse error; skipping")
        return None
    if not isinstance(loaded, dict):
        return None

    out: dict[str, Any] = {}

    for field in _TOP_LEVEL_SCALAR_FIELDS:
        coerced = _coerce_str(loaded.get(field))
        if coerced:
            out[field] = coerced

    for field in _TOP_LEVEL_LIST_FIELDS:
        coerced_list = _coerce_str_list(loaded.get(field))
        if coerced_list:
            out[field] = coerced_list

    # Authors are required by spec but we don't gate on that — a
    # malformed authors list shouldn't lose the rest of the file.
    authors = _parse_person_or_entity_list(loaded.get("authors"))
    if authors:
        out["authors"] = authors

    contacts = _parse_person_or_entity_list(loaded.get("contact"))
    if contacts:
        out["contact"] = contacts

    identifiers = _parse_identifiers(loaded.get("identifiers")) or []
    # Promote the deprecated top-level `doi` into `identifiers` if
    # not already present there. Keeps the canonical reading shape:
    # downstream walks `identifiers`, not `doi`. Also canonicalise the
    # top-level `doi` field itself so both shapes match the URL
    # convention every other catalog uses.
    legacy_doi = out.get("doi")
    if isinstance(legacy_doi, str):
        promoted_legacy = doi_iri(legacy_doi)
        if promoted_legacy:
            out["doi"] = promoted_legacy
            legacy_doi = promoted_legacy
        if not any(
            i.get("type") == "doi" and i.get("value") == legacy_doi
            for i in identifiers
        ):
            identifiers.append({"type": "doi", "value": legacy_doi})
    if identifiers:
        out["identifiers"] = identifiers

    preferred = _parse_reference(loaded.get("preferred-citation"))
    if preferred:
        out["preferred-citation"] = preferred

    references = _parse_references(loaded.get("references"))
    if references:
        out["references"] = references

    return out or None


__all__ = ["parse_citation_cff"]
