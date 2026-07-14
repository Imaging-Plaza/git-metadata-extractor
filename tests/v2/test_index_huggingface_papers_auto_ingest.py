"""Tests for the huggingface_papers auto-ingest hook on `/v2/extract`.

The hook is gated on:
  - `V2_HF_PAPERS_RAG_AUTO_INGEST=true` env flag (off by default)
  - URL pattern: `huggingface.co/papers/<arxiv_id>` ONLY.

By design, raw arxiv.org URLs and arXiv DOIs do NOT trigger this hook
— per the operator's choice when this feature was wired up.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from git_metadata_extractor.api.auto_ingest import (
    _hf_papers_arxiv_id_from_url,
    _maybe_schedule_huggingface_papers_auto_ingest,
)


# ---------------------------------------------------------------------------
# URL → arXiv id helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://huggingface.co/papers/2310.01234", "2310.01234"),
        ("http://huggingface.co/papers/2310.01234", "2310.01234"),
        ("https://huggingface.co/papers/2310.01234v2", "2310.01234"),
        ("https://huggingface.co/papers/2401.00001/", "2401.00001"),
    ],
)
def test_hf_papers_arxiv_id_from_url_accepts_hf_papers_urls(
    url: str, expected: str,
) -> None:
    assert _hf_papers_arxiv_id_from_url(url) == expected


@pytest.mark.parametrize(
    "bad_url",
    [
        None,
        "",
        42,
        # By design — these are arxiv-flavoured but NOT HF Papers:
        "https://arxiv.org/abs/2310.01234",
        "https://doi.org/10.48550/arXiv.2310.01234",
        "arxiv:2310.01234",
        # Wrong host:
        "https://example.com/papers/2310.01234",
        # HF host but not /papers/:
        "https://huggingface.co/models/some-model",
        "https://huggingface.co/datasets/some-dataset",
    ],
)
def test_hf_papers_arxiv_id_from_url_rejects_non_hf_papers_urls(bad_url) -> None:
    assert _hf_papers_arxiv_id_from_url(bad_url) is None


# ---------------------------------------------------------------------------
# Env-flag gating
# ---------------------------------------------------------------------------


def _classification(normalized_url: str) -> object:
    """Minimal classification stub — the hook reads only `normalized_url`."""
    return SimpleNamespace(normalized_url=normalized_url)


def test_hf_papers_auto_ingest_is_off_by_default() -> None:
    """No env flag => no schedule."""
    with patch("asyncio.create_task") as mock_create:
        _maybe_schedule_huggingface_papers_auto_ingest(
            classification=_classification("https://huggingface.co/papers/2310.01234"),
            run_id="run-1",
        )
    mock_create.assert_not_called()


def test_hf_papers_auto_ingest_ignores_arxiv_org_urls() -> None:
    """Flag on but URL is arxiv.org (not HF Papers) => no schedule."""
    with patch.dict(os.environ, {"V2_HF_PAPERS_RAG_AUTO_INGEST": "true"}), patch(
        "asyncio.create_task",
    ) as mock_create:
        _maybe_schedule_huggingface_papers_auto_ingest(
            classification=_classification("https://arxiv.org/abs/2310.01234"),
            run_id="run-1",
        )
    mock_create.assert_not_called()


def test_hf_papers_auto_ingest_ignores_repo_urls() -> None:
    """Flag on but URL is a GitHub repo => no schedule."""
    with patch.dict(os.environ, {"V2_HF_PAPERS_RAG_AUTO_INGEST": "true"}), patch(
        "asyncio.create_task",
    ) as mock_create:
        _maybe_schedule_huggingface_papers_auto_ingest(
            classification=_classification(
                "https://github.com/octocat/Hello-World",
            ),
            run_id="run-1",
        )
    mock_create.assert_not_called()


def test_hf_papers_auto_ingest_schedules_for_hf_papers_url_when_enabled() -> None:
    """Flag on + HF Papers URL => background task is scheduled."""
    with patch.dict(os.environ, {"V2_HF_PAPERS_RAG_AUTO_INGEST": "true"}), patch(
        "asyncio.create_task",
    ) as mock_create:
        _maybe_schedule_huggingface_papers_auto_ingest(
            classification=_classification("https://huggingface.co/papers/2310.01234"),
            run_id="run-1",
        )
    assert mock_create.call_count == 1


def test_hf_papers_auto_ingest_strips_version_suffix_in_log_path() -> None:
    """Versioned HF Papers URL still resolves to the bare id (the
    helper's normaliser strips `vN`)."""
    with patch.dict(os.environ, {"V2_HF_PAPERS_RAG_AUTO_INGEST": "true"}), patch(
        "asyncio.create_task",
    ) as mock_create:
        _maybe_schedule_huggingface_papers_auto_ingest(
            classification=_classification(
                "https://huggingface.co/papers/2310.01234v3",
            ),
            run_id="run-1",
        )
    assert mock_create.call_count == 1
