"""Fetch full ORCID records for seeded IDs, post-filter by scope, persist."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.index.orcid.ingest.orcid_client import build_orcid_provider
from src.index.orcid.ingest.scope import post_filter_record
from src.v2.ingest.providers.base import (
    ProviderError,
    ProviderNotFoundError,
)

if TYPE_CHECKING:
    from src.index.orcid.config import OrcidIndexConfig
    from src.index.orcid.storage.duckdb_store import OrcidDuckDBStore
    from src.v2.ingest.providers.base import ORCIDAffiliation, ORCIDRecord

LOGGER = logging.getLogger(__name__)


def ingest_persons(
    *,
    config: OrcidIndexConfig,
    store: OrcidDuckDBStore,
    scope: str,
    limit: int | None = None,
) -> dict[str, int]:
    """Fetch unfetched seeds, post-filter, persist. Returns counts summary."""
    provider = build_orcid_provider(config)
    summary = {"fetched": 0, "in_scope": 0, "out_of_scope": 0, "errors": 0}

    for seed in store.stream_seeds(only_unfetched=True):
        if limit is not None and summary["fetched"] >= limit:
            break
        orcid_id = seed["orcid_id"]
        discovered_via = seed["discovered_via"]
        try:
            record = provider.get_person_by_orcid(orcid_id)
        except ProviderNotFoundError:
            LOGGER.info("orcid not found, skipping: %s", orcid_id)
            summary["errors"] += 1
            continue
        except ProviderError as exc:
            LOGGER.warning("provider error for %s: %s", orcid_id, exc)
            summary["errors"] += 1
            continue
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("unexpected error for %s: %s", orcid_id, exc)
            summary["errors"] += 1
            continue
        summary["fetched"] += 1
        in_scope, reason = post_filter_record(
            record,
            scope=scope,  # type: ignore[arg-type]
            config=config,
            discovered_via=discovered_via,
        )
        _persist(
            store=store,
            record=record,
            in_scope=in_scope,
            scope_reason=reason,
            discovered_via=discovered_via,
        )
        summary["in_scope" if in_scope else "out_of_scope"] += 1
        if summary["fetched"] % 100 == 0:
            LOGGER.info("ingest progress: %s", summary)
    LOGGER.info("ingest complete: %s", summary)
    return summary


def _persist(
    *,
    store: OrcidDuckDBStore,
    record: ORCIDRecord,
    in_scope: bool,
    scope_reason: str | None,
    discovered_via: str,
) -> None:
    given, family = _split_name(record.get("name") or "")
    person_row = {
        "orcid_id": record["orcid_id"],
        "given_name": given,
        "family_name": family,
        "display_name": record.get("name"),
        "biography": None,  # ORCID provider doesn't surface biography today.
        "in_scope": in_scope,
        "scope_reason": scope_reason,
        "discovered_via": discovered_via,
    }
    raw_payload = {
        "orcid_id": record.get("orcid_id"),
        "name": record.get("name"),
        "employment": record.get("employment", []),
        "education": record.get("education", []),
        "affiliations": record.get("affiliations", []),
    }
    store.upsert_person(person_row, raw=raw_payload)
    store.replace_affiliations(
        "employments",
        record["orcid_id"],
        _affiliation_rows(record["orcid_id"], record.get("employment", [])),
    )
    store.replace_affiliations(
        "educations",
        record["orcid_id"],
        _affiliation_rows(record["orcid_id"], record.get("education", [])),
    )


def _affiliation_rows(
    orcid_id: str,
    affiliations: list[ORCIDAffiliation],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for seq, aff in enumerate(affiliations):
        rows.append(
            {
                "orcid_id": orcid_id,
                "seq": seq,
                "organization": aff.get("organization") or "",
                "org_ror": None,  # provider doesn't surface ROR yet.
                "department": aff.get("department"),
                "role": aff.get("role"),
                "start_date": aff.get("start_date"),
                "end_date": aff.get("end_date"),
            },
        )
    return rows


def _split_name(full_name: str) -> tuple[str | None, str | None]:
    """Best-effort split of a display name into (given, family)."""
    cleaned = full_name.strip()
    if not cleaned:
        return None, None
    if "," in cleaned:
        family, _, given = cleaned.partition(",")
        return given.strip() or None, family.strip() or None
    parts = cleaned.split()
    if len(parts) == 1:
        return None, parts[0]
    return " ".join(parts[:-1]), parts[-1]
