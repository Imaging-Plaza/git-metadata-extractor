"""Shared ORCID canonical-URL helper.

Companion to ``src/index/_shared/doi.py::doi_iri`` — same idea, same
shape, just for ORCID. The pipeline standardises on the canonical
``https://orcid.org/<BARE>`` URL form so downstream consumers see one
shape regardless of which agent / provider / catalog supplied the
identifier originally.

The bare form (``0000-0001-2345-6789``) historically lived alongside
the URL form in the codebase — ten separate ``_normalize_orcid``
implementations stripped to bare with slightly different validation
rules. This module collapses them to a single, consistent helper.

Strict-validation regex covers both checksum chars: the last digit of
an ORCID is `0-9` or `X` (the ISO/IEC 7064 mod-11 check digit). The
helper uppercases for canonical-form equality (``...000X`` ≠
``...000x`` if we don't).
"""

from __future__ import annotations

import re

_ORCID_BASE_URI = "https://orcid.org/"
_LEGACY_HTTP = "http://orcid.org/"

# ORCID Inc.'s published format. The check digit is computed mod-11
# over the first 15 digits; we only enforce the *shape* here — provider
# code can layer checksum validation on top via `ORCID_CHECKSUM_RE`
# below, but the shared helper stays cheap (no math).
ORCID_BARE_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[0-9X]$")


def orcid_iri(value: str | None) -> str | None:
    """Promote a bare or alternate-shape ORCID to canonical
    ``https://orcid.org/<BARE>`` URL form.

    Tolerates every input shape we've seen on the wire:
      - bare (``0000-0001-2345-6789``),
      - lowercase ``x`` checksum char (auto-uppercased),
      - ``orcid:``-prefixed (``orcid:0000-0001-...``),
      - legacy ``http://orcid.org/...`` (auto-upgraded to https),
      - trailing slash (stripped),
      - whitespace (stripped),
      - already-canonical URL form (idempotent).

    Returns ``None`` for:
      - non-string / empty / whitespace input,
      - any value whose extracted bare form doesn't match
        ``ORCID_BARE_RE``.

    Does NOT validate the mod-11 checksum — that's a provider concern
    where bogus IDs need to fail loudly. Callers that need it should
    layer their own check on top.
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.startswith(_LEGACY_HTTP):
        s = _ORCID_BASE_URI + s[len(_LEGACY_HTTP):]
    if s.lower().startswith(_ORCID_BASE_URI):
        s = _ORCID_BASE_URI + s[len(_ORCID_BASE_URI):]
        s = s.rstrip("/")
        bare = s[len(_ORCID_BASE_URI):]
    elif s.lower().startswith("orcid:"):
        bare = s[len("orcid:"):]
    else:
        bare = s
    bare = bare.strip().upper()
    if not ORCID_BARE_RE.fullmatch(bare):
        return None
    return _ORCID_BASE_URI + bare


def parse_orcid(value: str | None) -> str | None:
    """Inverse of ``orcid_iri`` — returns the bare ``0000-…`` form, or
    ``None`` on unparseable input. Useful for catalog backends whose
    storage column is constrained to the bare form.
    """
    iri = orcid_iri(value)
    if iri is None:
        return None
    return iri[len(_ORCID_BASE_URI):]


__all__ = ["ORCID_BARE_RE", "orcid_iri", "parse_orcid"]
