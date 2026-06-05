"""Structured (faceted) query surface for the federated layer.

This is the structured-query analog of ``federated_search``.  Only adapters
that expose a ``facet_query`` method (and set ``structured_query = True``) are
accessible via this module.

Public API
----------
structured_query_capable() -> list[str]
    Names of registered adapters that have a ``facet_query`` attribute.

run_structured_query(index, filters, **kw)
    Load the adapter for ``index`` and call its ``facet_query`` method.
    Raises ``ValueError`` with a clear message if the adapter has no
    ``facet_query`` method.
"""

from __future__ import annotations

from typing import Any


def structured_query_capable() -> list[str]:
    """Return the names of registered adapters that support ``facet_query``.

    Adapters are loaded lazily on the first call (same pattern as
    ``load_adapters``).
    """
    from src.index._federated.registry import load_adapters  # noqa: PLC0415

    return [
        adapter.name
        for adapter in load_adapters()
        if getattr(adapter, "facet_query", None) is not None
    ]


def run_structured_query(  # noqa: PLR0913
    index: str,
    filters: Any,
    *,
    text: str | None = None,
    sort: str = "start_date_desc",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Run a faceted query against *index*.

    Parameters
    ----------
    index:
        Short adapter name (e.g. ``"snsf"``).
    filters:
        An instance of the index-specific filter dataclass (e.g.
        ``GrantFilters``).
    text, sort, limit, offset:
        Forwarded verbatim to ``adapter.facet_query``.

    Returns
    -------
    dict
        The ``{"total": int, "results": [...]}`` shape returned by the
        adapter's ``facet_query`` implementation.

    Raises
    ------
    ValueError
        If the adapter exists but has no ``facet_query`` method.
    KeyError
        If no adapter is registered for *index*.
    """
    from src.index._federated.registry import REGISTRY, load_adapters  # noqa: PLC0415

    # Ensure the adapter module has been imported (self-registers on import).
    load_adapters(only=[index])

    if index not in REGISTRY:
        msg = f"no adapter registered for index {index!r}"
        raise KeyError(msg)

    adapter = REGISTRY[index]
    facet_query_fn = getattr(adapter, "facet_query", None)
    if facet_query_fn is None:
        msg = (
            f"index {index!r} has no facet_query method; "
            "use structured_query_capable() to list supported indices"
        )
        raise ValueError(msg)

    return facet_query_fn(filters, text=text, sort=sort, limit=limit, offset=offset)


__all__ = ["run_structured_query", "structured_query_capable"]
