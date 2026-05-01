"""Persist fetched README + (optional) card-adjacent files to disk.

Layout: `<INDEX_DATA_DIR>/huggingface/cards/<entity_type>/<repo_id>/`.
The README is always written (when present); the rest of the card files
only appear when `huggingface.full_cards` is enabled.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.index.huggingface.config import HuggingFaceIndexConfig
    from src.index.huggingface.ingest.hf_client import HFClient

LOGGER = logging.getLogger(__name__)


def card_dir_for(
    config: HuggingFaceIndexConfig,
    *,
    entity_type: str,
    repo_id: str,
) -> Path:
    return config.paths.cards_path_for(entity_type, repo_id)


def write_readme(
    config: HuggingFaceIndexConfig,
    *,
    entity_type: str,
    repo_id: str,
    readme: str,
) -> Path:
    target_dir = card_dir_for(config, entity_type=entity_type, repo_id=repo_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "README.md"
    target.write_text(readme, encoding="utf-8")
    return target


def maybe_snapshot_full_card(
    *,
    config: HuggingFaceIndexConfig,
    client: HFClient,
    entity_type: str,
    repo_id: str,
    repo_type: str,
) -> None:
    """If full_cards is enabled, pull README/JSON/YAML/MD files into the
    repo's local card dir. Weight files are always ignored."""
    if not config.huggingface.full_cards:
        return
    target_dir = card_dir_for(config, entity_type=entity_type, repo_id=repo_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    client.snapshot_card_files(repo_id, repo_type=repo_type, local_dir=target_dir)
