"""Conservative identity evidence shared by ROR enrichment and parent selection."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_GENERIC = frozenset(
    """
    foundry research lab labs laboratory laboratories group institute institut institutes
    university college center centre data hub science sciences scientific bio biotech
    biosciences genomics genome genomic cloud platform tech technology technologies systems
    system solutions computational national international corporation corp company
    foundation association network project team engineering medical medicine health
    information intelligence machine learning open source global services consulting
    consultants digital software therapeutics pharma pharmaceuticals biology biological
    dynamics analytics consortium initiative department dept division school faculty unit
    office holdings plaza of the and for de la el du des inc ltd gmbh llc co in at an on
    """.split(),  # noqa: SIM905 - keep the vocabulary readable
)

_SHARED_HOSTS = frozenset(
    """
    github.com github.io gitlab.com gitlab.io bitbucket.org readthedocs.io readthedocs.org
    wikipedia.org sites.google.com wordpress.com blogspot.com medium.com notion.site
    gitbook.io netlify.app vercel.app herokuapp.com
    """.split(),  # noqa: SIM905 - keep the vocabulary readable
)

_MIN_TOKEN_LENGTH = 3
_MIN_COMPACT_LENGTH = 4


def _tokens(value: str) -> set[str]:
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    return set(re.findall(r"[^\W\d_]+", value.casefold()))


def _compact(value: str) -> str:
    return "".join(re.findall(r"[^\W\d_]+", value.casefold()))


def organization_name_matches(
    record: dict[str, Any], *names: str | None, allow_generic_exact: bool = False,
) -> bool:
    """Require a whole name/alias/acronym or all distinctive candidate words.

    Evaluate aliases separately: unrelated words from different aliases must
    never combine into identity evidence. A single shared subject word is not
    enough when the candidate has another distinctive word (e.g. Housing).
    """
    queries = [name for name in names if isinstance(name, str) and name.strip()]
    query_tokens = set().union(*(_tokens(name) for name in queries))
    for acronym in record.get("acronyms") or []:
        if (
            isinstance(acronym, str)
            and len(acronym) >= _MIN_TOKEN_LENGTH
            and acronym.casefold() in query_tokens
        ):
            return True
    variants = [record.get("name")]
    for key in ("aliases", "labels"):
        variants.extend(record.get(key) or [])
    for item in variants:
        variant = (
            (item.get("label") or item.get("value")) if isinstance(item, dict) else item
        )
        if not isinstance(variant, str):
            continue
        distinctive = {
            word for word in _tokens(variant) if len(word) >= _MIN_TOKEN_LENGTH
        } - _GENERIC
        if distinctive and distinctive <= query_tokens:
            return True
        compact = _compact(re.sub(r"\([^)]*\)", "", variant))
        if (distinctive or allow_generic_exact) and len(compact) >= _MIN_COMPACT_LENGTH and any(
            compact == _compact(query) for query in queries
        ):
            return True
    return False


def _host(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        host = urlsplit(value if "://" in value else f"https://{value}").hostname
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    host = host.lower().rstrip(".").removeprefix("www.")
    if any(host == shared or host.endswith(f".{shared}") for shared in _SHARED_HOSTS):
        return None
    return host


def organization_website_matches(homepage: Any, record: dict[str, Any]) -> bool | None:
    """True for the same institutional host/subdomain, False for conflict.

    Missing or shared hosting yields no evidence. Compare full hosts, including
    their suffixes: acme.org and acme.com are distinct identities.
    """
    host = _host(homepage)
    candidates = {other for link in record.get("links") or [] if (other := _host(link))}
    if not host or not candidates:
        return None
    return any(host == other or host.endswith(f".{other}") for other in candidates)
