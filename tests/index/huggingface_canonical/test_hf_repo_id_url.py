"""HuggingFace models/datasets/spaces `repo_id` is the canonical URL (v3.0.0)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.index.huggingface_datasets.ingest.datasets import (
    _record_from_info as dataset_record,
)
from src.index.huggingface_models.ingest.models import _record_from_info as model_record
from src.index.huggingface_spaces.ingest.spaces import _record_from_info as space_record
from src.v2.canonicalization.huggingface import huggingface_iri


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (model_record, "https://huggingface.co/ZurichNLP/swissbert"),
        (dataset_record, "https://huggingface.co/datasets/ZurichNLP/swissbert"),
        (space_record, "https://huggingface.co/spaces/ZurichNLP/swissbert"),
    ],
)
def test_repo_id_is_canonical_url(builder, expected) -> None:
    rec = builder("ZurichNLP/swissbert", SimpleNamespace())
    assert rec.repo_id == expected


def test_canonicalizer_idempotent_and_kinds() -> None:
    assert huggingface_iri("a/b", "model") == "https://huggingface.co/a/b"
    assert huggingface_iri("a/b", "dataset") == "https://huggingface.co/datasets/a/b"
    assert huggingface_iri("x", "user") == "https://huggingface.co/x"
    assert huggingface_iri("2310.01234", "paper") == "https://huggingface.co/papers/2310.01234"
    # idempotent
    assert huggingface_iri("https://huggingface.co/datasets/a/b", "dataset") == "https://huggingface.co/datasets/a/b"
    assert huggingface_iri("", "model") is None
