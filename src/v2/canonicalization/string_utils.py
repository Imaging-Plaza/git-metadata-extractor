from __future__ import annotations

import re
import unicodedata

_PUNCTUATION_RE = re.compile(r"[^\w\s]+")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def collapse_whitespace(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def normalize_string(value: str) -> str:
    """Normalize free text for deterministic alias matching."""
    stripped = strip_accents(value.casefold())
    without_punctuation = _PUNCTUATION_RE.sub("", stripped)
    return collapse_whitespace(without_punctuation)

