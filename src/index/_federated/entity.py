"""Cross-index entity lookup: take an identifier, ask every adapter for matches."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Any

from src.index._federated.registry import EntityRecord, IndexAdapter, load_adapters

LOGGER = logging.getLogger(__name__)


def cross_index_lookup(
    identifier: str,
    *,
    indices: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve `identifier` against every registered (or named) adapter.

    Returns `{records: [EntityRecord-dicts...], by_index: {index: count}, errors: {...}}`.
    """
    adapters = load_adapters(only=indices) if indices is not None else load_adapters()

    records: list[EntityRecord] = []
    errors: dict[str, str] = {}
    by_index: dict[str, int] = {}

    def _run(adapter: IndexAdapter) -> tuple[str, list[EntityRecord] | str]:
        try:
            return (adapter.name, adapter.lookup(identifier))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("federated: adapter %s lookup failed: %s", adapter.name, exc)
            return (adapter.name, f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=max(len(adapters), 1)) as ex:
        futures = [ex.submit(_run, a) for a in adapters]
        for fut in as_completed(futures):
            name, result = fut.result()
            if isinstance(result, str):
                errors[name] = result
                continue
            by_index[name] = len(result)
            records.extend(result)

    return {
        "identifier": identifier,
        "records": [asdict(r) for r in records],
        "by_index": by_index,
        "errors": errors,
    }
