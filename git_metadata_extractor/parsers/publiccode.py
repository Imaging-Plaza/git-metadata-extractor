"""Parser for ``publiccode.yml`` / ``publiccode.yaml``.

The Public Code spec (``https://yml.publiccode.tools/``) is the
Italian-government-driven standard for declaring metadata about
open-source public-sector software. v0.4 is the current core schema
(``publiccodeYmlVersion: '0.4.0'``). The format carries strong
attribution / classification signal that the GME pipeline otherwise
has to derive from README parsing:

  - canonical project URL + landing page,
  - declared license + copyright owner + repo owner,
  - development status enum + softwareType enum,
  - maintainer contacts (name + email + affiliation),
  - intended-audience country list,
  - structured per-language descriptions (shortDescription,
    longDescription, features),
  - locale support matrix.

This parser is intentionally lenient: it tolerates extra keys (the
``it:`` country extension and other namespaces ship with arbitrary
sub-trees), drops fields that aren't a sensible type, and never
raises on a malformed file. Callers get either a fully populated dict
or ``None``.

Spec references:
  - https://yml.publiccode.tools/schema.core.html
  - https://yml.publiccode.tools/schema.country-it.html
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# Top-level fields that are always plain scalars / lists per the v0.4
# spec. Anything that lands here in a non-scalar shape is dropped (we
# pass through but flag a logger warning).
_TOP_LEVEL_SCALAR_FIELDS: tuple[str, ...] = (
    "publiccodeYmlVersion",
    "name",
    "url",
    "applicationSuite",
    "landingURL",
    "softwareVersion",
    "releaseDate",
    "logo",
    "monochromeLogo",
    "roadmap",
    "developmentStatus",
    "softwareType",
)
_TOP_LEVEL_LIST_OR_STR_FIELDS: tuple[str, ...] = (
    # `isBasedOn` is `string OR list[string]` in the spec; we coerce
    # the singular into a list for caller-side simplicity.
    "isBasedOn",
)
_TOP_LEVEL_LIST_FIELDS: tuple[str, ...] = (
    "platforms",
    "categories",
    "usedBy",
)


def _coerce_str(value: Any) -> str | None:
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    # PyYAML decodes `2024-05-01` as `datetime.date` and timestamped
    # forms as `datetime.datetime`. We stringify because everything
    # downstream (JSON-LD output, validation) expects the YAML scalar
    # form, not the Python type.
    import datetime
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return None


def _coerce_str_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else None
    if isinstance(value, list):
        out = [s.strip() for s in value if isinstance(s, str) and s.strip()]
        return out or None
    return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _parse_intended_audience(value: Any) -> dict[str, Any] | None:
    """``intendedAudience: { countries, unsupportedCountries, scope }``"""
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("countries", "unsupportedCountries", "scope"):
        coerced = _coerce_str_list(value.get(key))
        if coerced:
            out[key] = coerced
    return out or None


def _parse_legal(value: Any) -> dict[str, Any] | None:
    """``legal: { license, mainCopyrightOwner, repoOwner, authorsFile }``"""
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("license", "mainCopyrightOwner", "repoOwner", "authorsFile"):
        coerced = _coerce_str(value.get(key))
        if coerced:
            out[key] = coerced
    return out or None


def _parse_maintenance(value: Any) -> dict[str, Any] | None:
    """``maintenance: { type, contractors, contacts }``

    ``type`` is one of: internal | community | contract | none.
    ``contacts`` is a list of ``{ name, email, affiliation, phone }``
    dicts; we keep the records intact (caller may want any of them).
    """
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    type_ = _coerce_str(value.get("type"))
    if type_:
        out["type"] = type_
    contractors = value.get("contractors")
    if isinstance(contractors, list):
        clean_contractors = []
        for item in contractors:
            if not isinstance(item, dict):
                continue
            entry = {
                k: _coerce_str(item.get(k))
                for k in ("name", "until", "email", "website", "affiliation")
                if _coerce_str(item.get(k))
            }
            if entry:
                clean_contractors.append(entry)
        if clean_contractors:
            out["contractors"] = clean_contractors
    contacts = value.get("contacts")
    if isinstance(contacts, list):
        clean_contacts = []
        for item in contacts:
            if not isinstance(item, dict):
                continue
            entry = {
                k: _coerce_str(item.get(k))
                for k in ("name", "email", "affiliation", "phone")
                if _coerce_str(item.get(k))
            }
            if entry:
                clean_contacts.append(entry)
        if clean_contacts:
            out["contacts"] = clean_contacts
    return out or None


def _parse_localisation(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    ready = _coerce_bool(value.get("localisationReady"))
    if ready is not None:
        out["localisationReady"] = ready
    langs = _coerce_str_list(value.get("availableLanguages"))
    if langs:
        out["availableLanguages"] = langs
    return out or None


def _parse_depends_on(value: Any) -> dict[str, Any] | None:
    """``dependsOn: { open: [...], proprietary: [...], hardware: [...] }``

    Each sub-list holds objects of shape
    ``{ name, versionMin, versionMax, optional }``.
    """
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for bucket in ("open", "proprietary", "hardware"):
        items = value.get(bucket)
        if not isinstance(items, list):
            continue
        clean_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            entry: dict[str, Any] = {}
            for k in ("name", "versionMin", "versionMax", "version"):
                coerced = _coerce_str(item.get(k))
                if coerced:
                    entry[k] = coerced
            optional = _coerce_bool(item.get("optional"))
            if optional is not None:
                entry["optional"] = optional
            if entry:
                clean_items.append(entry)
        if clean_items:
            out[bucket] = clean_items
    return out or None


def _parse_description(value: Any) -> dict[str, Any] | None:
    """``description: { <lang>: { ... shortDescription, longDescription,
    features, screenshots, videos, awards, genericName, documentation,
    apiDocumentation, localisedName } }``

    Returns the per-language map verbatim (after cleaning). Caller is
    free to pick its preferred language (`en`/`it`/…).
    """
    if not isinstance(value, dict):
        return None
    out: dict[str, dict[str, Any]] = {}
    for lang, entry in value.items():
        if not isinstance(lang, str) or not isinstance(entry, dict):
            continue
        clean: dict[str, Any] = {}
        for k in (
            "localisedName",
            "genericName",
            "shortDescription",
            "longDescription",
            "documentation",
            "apiDocumentation",
        ):
            coerced = _coerce_str(entry.get(k))
            if coerced:
                clean[k] = coerced
        for k in ("features", "screenshots", "videos", "awards"):
            coerced_list = _coerce_str_list(entry.get(k))
            if coerced_list:
                clean[k] = coerced_list
        if clean:
            out[lang.strip()] = clean
    return out or None


def parse_publiccode(content: str | None) -> dict[str, Any] | None:
    """Parse a ``publiccode.yml`` (or ``.yaml``) document.

    Returns a dict whose top-level keys are the publiccode field names
    (camelCase, per the spec) for fields that were present and well-
    typed in the input. Always lossy by design: malformed sub-trees
    are silently dropped rather than surfaced as parse errors.

    Returns ``None`` when:
      - ``content`` is None / empty,
      - the YAML payload doesn't load to a mapping,
      - the YAML is malformed.

    Country extensions (``it:`` etc.) and any other top-level keys not
    recognised by the v0.4 core schema are passed through verbatim as
    long as their value is a dict — callers who want to inspect e.g.
    ``it.riuso.codiceIPA`` can do so without losing fidelity.
    """
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError:
        logger.warning("publiccode.yml: YAML parse error; skipping")
        return None
    if not isinstance(loaded, dict):
        return None

    out: dict[str, Any] = {}

    for field in _TOP_LEVEL_SCALAR_FIELDS:
        coerced = _coerce_str(loaded.get(field))
        if coerced:
            out[field] = coerced

    for field in _TOP_LEVEL_LIST_OR_STR_FIELDS:
        coerced_list = _coerce_str_list(loaded.get(field))
        if coerced_list:
            out[field] = coerced_list

    for field in _TOP_LEVEL_LIST_FIELDS:
        coerced_list = _coerce_str_list(loaded.get(field))
        if coerced_list:
            out[field] = coerced_list

    audience = _parse_intended_audience(loaded.get("intendedAudience"))
    if audience:
        out["intendedAudience"] = audience

    description = _parse_description(loaded.get("description"))
    if description:
        out["description"] = description

    legal = _parse_legal(loaded.get("legal"))
    if legal:
        out["legal"] = legal

    maintenance = _parse_maintenance(loaded.get("maintenance"))
    if maintenance:
        out["maintenance"] = maintenance

    localisation = _parse_localisation(loaded.get("localisation"))
    if localisation:
        out["localisation"] = localisation

    depends_on = _parse_depends_on(loaded.get("dependsOn"))
    if depends_on:
        out["dependsOn"] = depends_on

    # Pass through any country-extension blocks (it:, fr:, etc.) and
    # other dict-valued top-level fields verbatim. Helps preserve
    # forward compatibility — when v0.5 lands with a new top-level
    # block we don't need to recompile to surface it.
    recognised = (
        set(_TOP_LEVEL_SCALAR_FIELDS)
        | set(_TOP_LEVEL_LIST_OR_STR_FIELDS)
        | set(_TOP_LEVEL_LIST_FIELDS)
        | {
            "intendedAudience", "description", "legal",
            "maintenance", "localisation", "dependsOn",
        }
    )
    for key, value in loaded.items():
        if not isinstance(key, str) or key in recognised:
            continue
        if isinstance(value, dict):
            out[key] = value

    return out or None


__all__ = ["parse_publiccode"]
