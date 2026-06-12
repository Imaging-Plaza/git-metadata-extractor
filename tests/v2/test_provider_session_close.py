# tests/v2/test_provider_session_close.py
"""Bug 03 (S3): provider HTTP sessions were never closed, leaking a urllib3
connection pool once per extraction. Providers + ProviderSet now expose close().
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.v2.agents.models import ProviderSet
from src.v2.ingest.providers.orcid_provider import RealORCIDProvider
from src.v2.ingest.providers.ror_provider import RealRORProvider


def test_orcid_provider_close_closes_and_nulls_session():
    provider = RealORCIDProvider()
    session = MagicMock()
    provider._session = session
    provider.close()
    session.close.assert_called_once()
    assert provider._session is None
    provider.close()  # idempotent — no session, no error


def test_ror_provider_close_closes_and_nulls_session():
    provider = RealRORProvider()
    session = MagicMock()
    provider._session = session
    provider.close()
    session.close.assert_called_once()
    assert provider._session is None


def test_provider_set_close_calls_closeable_and_skips_others():
    flags = {"closed": False}

    class _Closeable:
        def close(self) -> None:
            flags["closed"] = True

    class _NoClose:
        pass

    ProviderSet(github=_NoClose(), orcid=_Closeable()).close()  # must not raise
    assert flags["closed"] is True
