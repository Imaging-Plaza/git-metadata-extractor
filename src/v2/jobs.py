"""Persistent job store for the v2 async POST /extract pattern.

Records are persisted to the same SQLite-backed `ProviderCache` used elsewhere
in v2 — a separate cache namespace keeps job records from colliding with
provider response keys.
"""

from __future__ import annotations

from src.v2.api_models import V2ExtractJob
from src.v2.ingest.cache import ProviderCache

JOB_NAMESPACE = "v2-extract-job"


class JobStore:
    """Read/write `V2ExtractJob` records via `ProviderCache`."""

    def __init__(self, cache: ProviderCache) -> None:
        self._cache = cache

    @staticmethod
    def make_key(job_id: str) -> str:
        return ProviderCache.make_key(JOB_NAMESPACE, "record", job_id=job_id)

    def get(self, job_id: str) -> V2ExtractJob | None:
        raw = self._cache.get(self.make_key(job_id))
        if not isinstance(raw, dict):
            return None
        try:
            return V2ExtractJob.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None

    def set(self, job: V2ExtractJob) -> None:
        self._cache.set(
            self.make_key(job.job_id),
            job.model_dump(mode="json", exclude_none=True),
        )


__all__ = ["JOB_NAMESPACE", "JobStore"]
