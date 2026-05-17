"""Persistent job store for `POST /v2/indices/<name>/ingest` async ingests.

Mirrors :class:`src.v2.jobs.JobStore` but keeps a separate namespace so the
extract-job and index-ingest-job keys never collide in the shared
``ProviderCache``.
"""

from __future__ import annotations

from src.v2.api_models import IndexIngestJob
from src.v2.ingest.cache import ProviderCache

JOB_NAMESPACE = "v2-index-ingest-job"


class IndexIngestJobStore:
    """Read/write :class:`IndexIngestJob` records via :class:`ProviderCache`."""

    def __init__(self, cache: ProviderCache) -> None:
        self._cache = cache

    @staticmethod
    def make_key(job_id: str) -> str:
        return ProviderCache.make_key(JOB_NAMESPACE, "record", job_id=job_id)

    def get(self, job_id: str) -> IndexIngestJob | None:
        raw = self._cache.get(self.make_key(job_id))
        if not isinstance(raw, dict):
            return None
        try:
            return IndexIngestJob.model_validate(raw)
        except Exception:  # noqa: BLE001 — return None on schema drift
            return None

    def set(self, job: IndexIngestJob) -> None:
        self._cache.set(
            self.make_key(job.job_id),
            job.model_dump(mode="json", exclude_none=True),
        )


__all__ = ["JOB_NAMESPACE", "IndexIngestJobStore"]
