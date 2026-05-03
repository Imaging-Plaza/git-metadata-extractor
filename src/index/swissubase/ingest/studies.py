"""Project SWISSUbase JSON payloads → DuckDB rows and persist.

Pipeline per study:

    1. catalogue search yields a thin item with ``studyVersionId``.
    2. fetch ``overview-block`` (institutions, persons, disciplines,
       dates, version) and ``main`` (abstract, title, statusCode).
    3. ``dynamic-blocks`` is fetched but only stored raw — its content
       blocks (Methods, Datasets, Funding, ...) are loaded lazily by the
       SPA via per-block endpoints we haven't reverse-engineered yet.
       Datasets/funding parsing is therefore best-effort for this pass.
    4. project everything into ``StudyRow`` + ``PersonRow`` +
       ``InstitutionRow`` shapes, compute ``affiliation_match`` against
       the configured scope, and upsert.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.index.swissubase.ingest.scope import Scope

if TYPE_CHECKING:
    from src.index.swissubase.config import SwissubaseIndexConfig
    from src.index.swissubase.ingest.swissubase_client import SwissubaseClient
    from src.index.swissubase.storage.duckdb_store import SwissubaseStore

LOGGER = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = _DATE_RE.match(raw.strip())
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2) or 1)
    day = int(m.group(3) or 1)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _slugify(value: str) -> str:
    return _NON_WORD_RE.sub("-", value.lower()).strip("-")


def _select_localized(block: Any, language: str) -> str | None:
    """Pick the language variant or fall back to the first available."""
    if block is None:
        return None
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        if language in block and isinstance(block[language], str):
            return block[language]
        for fallback in ("en", "fr", "de", "it"):
            if fallback in block and isinstance(block[fallback], str):
                return block[fallback]
        for v in block.values():
            if isinstance(v, str) and v.strip():
                return v
    return None


def _build_study_url(base_url: str, language: str, study_version_id: str) -> str:
    return f"{base_url.rstrip('/')}/{language}/catalogue/studies/{study_version_id}"


def _institution_key(name: str) -> str:
    slug = _slugify(name) if name else ""
    return f"name:{slug}" if slug else ""


def _institution_address(addr: dict[str, Any] | None) -> str | None:
    if not isinstance(addr, dict):
        return None
    parts = [
        addr.get("address1"),
        addr.get("address2"),
        addr.get("postalNumber"),
        addr.get("city"),
        addr.get("country"),
    ]
    rendered = ", ".join(p for p in parts if isinstance(p, str) and p.strip())
    return rendered or None


def _person_key(person: dict[str, Any]) -> str:
    """Stable key for a person.

    SWISSUbase exposes ``personId`` (numeric) and ``refNumber`` per
    person; we use ``swissubase:person:{personId}`` as the canonical
    identifier so downstream consumers can dedupe across studies.
    """
    pid = person.get("personId")
    if pid:
        return f"swissubase:person:{pid}"
    first = (person.get("firstName") or "").strip()
    last = (person.get("lastName") or "").strip()
    slug = _slugify(f"{first} {last}".strip())
    return f"name:{slug}" if slug else ""


def _person_display_name(person: dict[str, Any]) -> str:
    first = (person.get("firstName") or "").strip()
    last = (person.get("lastName") or "").strip()
    full = f"{first} {last}".strip()
    return full or person.get("displayName") or ""


def _project_institutions(
    overview: dict[str, Any] | None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return (row, raw) pairs ready for upsert. Skip unkeyed entries."""
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if not isinstance(overview, dict):
        return out
    for inst in overview.get("institutions") or []:
        if not isinstance(inst, dict):
            continue
        # `fullName` is sometimes a list of strings; normalize to a string.
        name_field = inst.get("fullName") or inst.get("name")
        if isinstance(name_field, list):
            name = next(
                (n for n in name_field if isinstance(n, str) and n.strip()),
                None,
            )
        elif isinstance(name_field, str):
            name = name_field.strip() or None
        else:
            name = None
        if not name:
            continue
        ikey = _institution_key(name)
        if not ikey:
            continue
        address = _institution_address(inst.get("mainAddress"))
        out.append(
            (
                {
                    "institution_key": ikey,
                    "name": name,
                    "address": address,
                    "ror_id": None,  # SWISSUbase doesn't expose ROR refs.
                    "source_url": None,
                },
                inst,
            ),
        )
    return out


def _project_persons(
    overview: dict[str, Any] | None,
) -> list[tuple[dict[str, Any], dict[str, Any], str | None, int]]:
    """Yield (row, raw, role, position) tuples for each person."""
    out: list[tuple[dict[str, Any], dict[str, Any], str | None, int]] = []
    if not isinstance(overview, dict):
        return out
    for person in overview.get("persons") or []:
        if not isinstance(person, dict):
            continue
        pkey = _person_key(person)
        if not pkey:
            continue
        display = _person_display_name(person)
        if person.get("isPI"):
            role = "Principal investigator"
        elif person.get("active") is False:
            role = "Former collaborator"
        else:
            role = "Author"
        position = int(person.get("sort") or 0)
        out.append(
            (
                {
                    "person_key": pkey,
                    "display_name": display,
                    "orcid": None,
                    "affiliation": None,
                    "source_url": None,
                },
                person,
                role,
                position,
            ),
        )
    return out


def _disciplines(overview: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Return (main, sub) discipline labels.

    swissUbase emits a hierarchical list (top → leaf). We take the first
    element as ``main`` and the second (when present) as ``sub``.
    """
    if not isinstance(overview, dict):
        return (None, None)
    raw = overview.get("disciplines")
    if not isinstance(raw, list):
        return (None, None)
    flat = [d for d in raw if isinstance(d, str) and d.strip()]
    main = flat[0] if flat else None
    sub = flat[1] if len(flat) > 1 else None
    return (main, sub)


def _format_version(overview: dict[str, Any] | None) -> str | None:
    if not isinstance(overview, dict):
        return None
    block = overview.get("versionNumber")
    if not isinstance(block, dict):
        return None
    parts = [
        str(block.get("majorVersion")) if block.get("majorVersion") is not None else None,
        str(block.get("minorVersion")) if block.get("minorVersion") is not None else None,
    ]
    rendered = ".".join(p for p in parts if p)
    return rendered or None


def project_study(
    *,
    config: SwissubaseIndexConfig,
    scope: Scope,
    item: dict[str, Any],
    overview: dict[str, Any] | None,
    main: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Project a SWISSUbase study payload into a ``StudyRow`` dict.

    Returns ``None`` when the catalogue item has no ``studyVersionId``
    (defensive — every observed item has one).
    """
    sid = item.get("studyVersionId") or item.get("studyId")
    if sid is None:
        return None
    study_id = str(sid)

    title = (
        _select_localized(main.get("title") if isinstance(main, dict) else None, config.catalogue.language)
        or _select_localized(overview.get("title") if isinstance(overview, dict) else None, config.catalogue.language)
        or item.get("studyVersionMainTitle")
        or _select_localized(item.get("studyVersionTitle"), config.catalogue.language)
    )
    abstract = (
        _select_localized(main.get("abstract") if isinstance(main, dict) else None, config.catalogue.language)
        or _select_localized(item.get("abstract"), config.catalogue.language)
    )
    main_disc, sub_disc = _disciplines(overview)
    institutions = _project_institutions(overview)
    institution_strings = [r["name"] for r, _ in institutions if r.get("name")]
    affiliation_match = scope.matches(*institution_strings)

    return {
        "study_id": study_id,
        "ref": str(overview.get("referenceNumber"))
            if isinstance(overview, dict) and overview.get("referenceNumber") is not None
            else (str(item.get("referenceNumber")) if item.get("referenceNumber") is not None else None),
        "title": title,
        "description": abstract,
        "description_language": (
            (main.get("metadataLanguageCode") if isinstance(main, dict) else None)
            or config.catalogue.language
        ),
        "start_date": _parse_date(overview.get("startDate") if isinstance(overview, dict) else None),
        "end_date": _parse_date(
            (overview.get("endDate") if isinstance(overview, dict) else None) or item.get("endDate"),
        ),
        "progress": (overview.get("progressString") if isinstance(overview, dict) else None),
        "main_discipline": main_disc,
        "sub_discipline": sub_disc,
        "version": _format_version(overview),
        "data_availability": (
            overview.get("dataAccessInformation") if isinstance(overview, dict) else None
        ) and json.dumps(overview.get("dataAccessInformation"), ensure_ascii=False),
        "dataset_count": item.get("datasetsCount"),
        "affiliation_match": affiliation_match,
        "source_url": _build_study_url(
            config.catalogue.base_url, config.catalogue.language, study_id,
        ),
    }


def persist_study(
    *,
    store: SwissubaseStore,
    study_row: dict[str, Any],
    overview: dict[str, Any] | None,
    dynamic_blocks: Any,
) -> None:
    """Persist a fully projected study + its institutions/persons + edges."""
    store.upsert_study(
        study_row,
        raw_overview=overview if isinstance(overview, dict) else None,
        raw_dynamic_blocks=(
            dynamic_blocks if isinstance(dynamic_blocks, (dict, list)) else None
        ),
    )

    institutions = _project_institutions(overview)
    for inst_row, inst_raw in institutions:
        store.upsert_institution(inst_row, raw=inst_raw)
    if institutions:
        store.upsert_study_institutions(
            study_row["study_id"],
            (r["institution_key"] for r, _ in institutions),
        )

    persons = _project_persons(overview)
    for person_row, person_raw, _, _ in persons:
        store.upsert_person(person_row, raw=person_raw)
    if persons:
        store.upsert_study_persons(
            study_row["study_id"],
            ((row["person_key"], role, position) for row, _, role, position in persons),
        )


# ---- Orchestration ----------------------------------------------------


def _state_path(config: SwissubaseIndexConfig, scope_name: str) -> Path:
    return config.paths.state_dir / f"ingest_{scope_name}.json"


def _load_state(config: SwissubaseIndexConfig, scope_name: str) -> dict[str, Any]:
    path = _state_path(config, scope_name)
    if not path.exists():
        return {"last_id": 0, "completed": False, "ingested": 0, "scope_matched": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        LOGGER.warning("could not parse %s; restarting state", path)
        return {"last_id": 0, "completed": False, "ingested": 0, "scope_matched": 0}


def _save_state(
    config: SwissubaseIndexConfig,
    scope_name: str,
    state: dict[str, Any],
) -> None:
    _state_path(config, scope_name).write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def ingest_studies(
    *,
    config: SwissubaseIndexConfig,
    client: SwissubaseClient,
    store: SwissubaseStore,
    scope: Scope,
    limit: int | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Enumerate studyVersionIds in [id_start..id_end] and persist each one.

    The catalogue search-studies endpoint silently caps at ~250 items per
    query (and ~1000 with smaller pagesize) regardless of filter combos, so
    we enumerate by ID directly. Per-study endpoints have no such cap.

    Resumable: ``last_id`` checkpoint is saved every 50 studies. A second
    run without ``--refresh`` resumes from there.
    """
    state = (
        _load_state(config, scope.name)
        if not refresh
        else {"last_id": 0, "completed": False, "ingested": 0, "scope_matched": 0}
    )
    start_id = max(int(state.get("last_id") or 0) + 1, config.catalogue.id_start)
    end_id = config.catalogue.id_end
    ingested = int(state.get("ingested") or 0)
    matched = int(state.get("scope_matched") or 0)
    failures: list[dict[str, str]] = []

    LOGGER.info(
        "swissubase ingest starting: id_range=[%d..%d] resume_from=%d "
        "current_ingested=%d current_matched=%d",
        config.catalogue.id_start, end_id, start_id, ingested, matched,
    )

    yielded_session = 0
    for sid_int, overview in client.iter_studies_by_id(
        start_id=start_id, end_id=end_id,
    ):
        sid = str(sid_int)
        try:
            main = client.fetch_study_main(sid)
        except Exception as exc:  # noqa: BLE001
            failures.append({"study_id": sid, "stage": "main", "error": str(exc)[:300]})
            LOGGER.warning("main fetch failed for %s: %s", sid, exc)
            main = None
        try:
            dynamic_blocks = client.fetch_study_dynamic_blocks(sid)
        except Exception as exc:  # noqa: BLE001
            LOGGER.info("dynamic-blocks fetch failed for %s: %s", sid, exc)
            dynamic_blocks = None

        # Catalogue-level fields (datasetsCount, endDate) aren't available
        # via the per-study endpoints, so we pass an empty stub item; all
        # the rich data comes from `overview` + `main`.
        row = project_study(
            config=config, scope=scope, item={"studyVersionId": sid_int},
            overview=overview if isinstance(overview, dict) else None,
            main=main if isinstance(main, dict) else None,
        )
        if row is None:
            continue
        persist_study(
            store=store,
            study_row=row,
            overview=overview if isinstance(overview, dict) else None,
            dynamic_blocks=dynamic_blocks,
        )
        ingested += 1
        yielded_session += 1
        if row.get("affiliation_match"):
            matched += 1
        if ingested % 50 == 0:
            state["last_id"] = sid_int
            state["ingested"] = ingested
            state["scope_matched"] = matched
            _save_state(config, scope.name, state)
            LOGGER.info(
                "swissubase ingest progress: id=%d ingested=%d %s-matched=%d",
                sid_int, ingested, scope.name, matched,
            )
        if limit is not None and yielded_session >= limit:
            break

    state["last_id"] = end_id
    state["ingested"] = ingested
    state["scope_matched"] = matched
    state["completed"] = (limit is None)
    _save_state(config, scope.name, state)
    return {
        "ingested": ingested,
        "scope_matched": matched,
        "scope": scope.name,
        "id_range": [config.catalogue.id_start, end_id],
        "failures": failures,
    }
