"""Fetch + persist one Docker Hub repository (image)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.index.dockerhub.models import DockerhubRepoRecord
from src.v2.canonicalization.dockerhub import dockerhub_iri

if TYPE_CHECKING:
    from src.index.dockerhub.config import DockerhubIndexConfig
    from src.index.dockerhub.ingest.dockerhub_client import DockerHubClient
    from src.index.dockerhub.storage.duckdb_store import DockerhubStore

LOGGER = logging.getLogger(__name__)


def normalize_repo_id(identifier: str) -> tuple[str, str]:
    """Split a Docker Hub image reference into ``(namespace, name)``.

    Accepts:
      - ``namespace/name`` → as-is
      - bare ``name`` (official image) → ``library/name``
      - a full ``https://hub.docker.com/r/<ns>/<name>`` or
        ``/_/<name>`` (official) URL
      - a ``docker.io/...`` / ``registry-1.docker.io/...`` pull reference,
        optionally with a ``:tag`` (the tag is dropped — we index repos)
    """
    ref = identifier.strip()
    for prefix in (
        "https://hub.docker.com/r/",
        "http://hub.docker.com/r/",
        "https://hub.docker.com/_/",
        "http://hub.docker.com/_/",
        "https://hub.docker.com/",
        "docker.io/",
        "registry-1.docker.io/",
    ):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
            break
    ref = ref.strip("/")
    ref = ref.split(":", 1)[0]  # drop any :tag
    if not ref:
        message = f"Empty Docker Hub image reference: {identifier!r}"
        raise ValueError(message)
    if "/" in ref:
        namespace, name = ref.split("/", 1)
        # `_/<name>` is Docker's official-image alias for `library/<name>`.
        if namespace == "_":
            namespace = "library"
    else:
        namespace, name = "library", ref
    return namespace, name


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _record_from_payload(
    *, namespace: str, name: str, payload: dict[str, Any], tags: list[str],
) -> DockerhubRepoRecord:
    return DockerhubRepoRecord(
        # v3.0.0: id is the canonical Docker Hub URL (namespace/name kept bare).
        repo_id=dockerhub_iri(namespace, name) or f"{namespace}/{name}",
        namespace=namespace,
        name=name,
        description=payload.get("description") or None,
        full_description=payload.get("full_description") or None,
        is_official=(payload.get("namespace") == "library")
        or bool(payload.get("is_official")),
        is_automated=bool(payload.get("is_automated")),
        is_private=bool(payload.get("is_private")),
        star_count=int(payload.get("star_count") or 0),
        pull_count=int(payload.get("pull_count") or 0),
        status=str(payload["status"]) if payload.get("status") is not None else None,
        last_updated=_parse_dt(payload.get("last_updated")),
        date_registered=_parse_dt(payload.get("date_registered")),
        tags=tags,
        raw=payload,
    )


def ingest_single_image(
    *,
    config: DockerhubIndexConfig,
    store: DockerhubStore,
    client: DockerHubClient,
    image_ref: str,
) -> str:
    """Fetch + upsert one Docker Hub repository. Returns
    ``"ingested" | "skipped_404"``."""
    namespace, name = normalize_repo_id(image_ref)
    payload = client.get_repository(namespace, name)
    if not isinstance(payload, dict):
        LOGGER.warning(
            "ingest skip: dockerhub image not found or unreachable: %s/%s",
            namespace, name,
        )
        return "skipped_404"
    tags = client.get_tags(namespace, name, limit=config.dockerhub.tags_limit)
    record = _record_from_payload(
        namespace=namespace, name=name, payload=payload, tags=tags,
    )
    store.upsert_image(record)
    LOGGER.info(
        "ingested dockerhub image %s/%s (pulls=%d stars=%d tags=%d)",
        namespace, name, record.pull_count, record.star_count, len(tags),
    )
    return "ingested"
