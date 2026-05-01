"""Regex-based GitHub/HuggingFace URL extraction."""

from __future__ import annotations

from pathlib import Path

from src.index.infoscience.extract_matches import (
    _canonicalise,
    classify_huggingface_url,
    extract_matches,
)
from src.index.infoscience.config import (
    ChunkingConfig,
    FilterConfig,
    InfoscienceConfig,
    InfoscienceIndexConfig,
    QdrantConfig,
    RcpConfig,
)
from src.index.infoscience.paths import (
    matches_path,
    raw_items_dir,
    text_dir,
)


def _stub_config() -> InfoscienceIndexConfig:
    return InfoscienceIndexConfig(
        rcp=RcpConfig(
            base_url="https://stub/v1",
            embedding_model="Qwen/Qwen3-Embedding-8B",
            embedding_dim=4096,
            query_instruction="x",
            reranker_model="Qwen/Qwen3-Reranker-8B",
        ),
        infoscience=InfoscienceConfig(base_url="https://stub/api"),
        filter=FilterConfig(terms=["github.com", "huggingface.co", "hf.co"]),
        chunking=ChunkingConfig(),
        qdrant=QdrantConfig(),
        data_dir=Path("/tmp"),
    )


def test_classify_hf_model() -> None:
    kind, canonical = classify_huggingface_url("https://huggingface.co/Qwen/Qwen3-Embedding-8B")
    assert kind == "model"
    assert canonical == "https://huggingface.co/Qwen/Qwen3-Embedding-8B"


def test_classify_hf_dataset_with_subpath() -> None:
    kind, canonical = classify_huggingface_url(
        "https://huggingface.co/datasets/squad/viewer/train",
    )
    assert kind == "dataset"
    # HF dataset slugs can be `owner/name`; keep the two head segments.
    assert canonical == "https://huggingface.co/datasets/squad/viewer"


def test_classify_hf_dataset_simple() -> None:
    kind, canonical = classify_huggingface_url(
        "https://huggingface.co/datasets/openai/squad",
    )
    assert kind == "dataset"
    assert canonical == "https://huggingface.co/datasets/openai/squad"


def test_classify_hf_space() -> None:
    kind, canonical = classify_huggingface_url("https://huggingface.co/spaces/foo/bar")
    assert kind == "space"
    assert canonical == "https://huggingface.co/spaces/foo/bar"


def test_classify_hf_short_host() -> None:
    kind, canonical = classify_huggingface_url("https://hf.co/openai-community/gpt2")
    assert kind == "model"
    # _hostname check returns hf.co; canonical hard-codes huggingface.co.
    assert "openai-community/gpt2" in canonical


def test_canonicalise_keeps_unsupported_github_url() -> None:
    out = _canonicalise("https://github.com/sdsc-ordes/gimie/issues/42")
    assert out == "https://github.com/sdsc-ordes/gimie/issues/42"


def test_canonicalise_normalises_repo_url() -> None:
    out = _canonicalise("https://github.com/sdsc-ordes/gimie.git")
    assert out == "https://github.com/sdsc-ordes/gimie"


def test_canonicalise_ignores_unrelated_host() -> None:
    assert _canonicalise("https://example.com/x") is None


def test_extract_matches_end_to_end(
    isolated_data_dir, sample_extracted_text: str, article_json: dict,
) -> None:
    # Wire a single text + a corresponding raw item into the data dir.
    uuid = article_json["uuid"]
    (text_dir() / f"{uuid}.txt").write_text(sample_extracted_text, encoding="utf-8")
    (raw_items_dir() / f"{uuid}.json").write_text(__import__("json").dumps(article_json),
                                                  encoding="utf-8")

    summary = extract_matches(_stub_config())
    assert summary["with_matches"] == 1
    line = matches_path().read_text(encoding="utf-8").strip()
    assert uuid in line
    # The fixture mentions both github.com and huggingface.co.
    assert "github.com" in line
    assert "huggingface.co" in line
