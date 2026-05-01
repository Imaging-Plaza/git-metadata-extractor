"""Thin wrapper around `huggingface_hub.HfApi`.

We only expose the calls the ingest pipeline needs (list/info/download
README) and centralise the auth + base-url config in one place.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Iterable

from huggingface_hub import HfApi
from huggingface_hub.utils import (
    EntryNotFoundError,
    GatedRepoError,
    HfHubHTTPError,
    RepositoryNotFoundError,
)

if TYPE_CHECKING:
    from src.index.huggingface.config import HuggingFaceIndexConfig

LOGGER = logging.getLogger(__name__)

# Files we never want to download even when full_cards is True. Keeping
# this list tight avoids accidentally pulling multi-GB weight files.
WEIGHT_PATTERNS: tuple[str, ...] = (
    "*.bin",
    "*.safetensors",
    "*.pt",
    "*.ckpt",
    "*.gguf",
    "*.onnx",
    "*.h5",
    "*.msgpack",
    "*.tflite",
    "*.pkl",
    "*.npz",
)

# Files we keep when full_cards is True.
CARD_PATTERNS: tuple[str, ...] = (
    "README.md",
    "README",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.md",
)

# Common non-README aliases on HF; tried in order.
README_FILENAME_FALLBACKS: tuple[str, ...] = ("README.md", "README", "Readme.md")


class HFClient:
    """Lifecycle + auth + thin HfApi wrapper.

    All methods are synchronous — `huggingface_hub` is a sync client and we
    don't gain much from forcing it through asyncio.to_thread for the
    read-mostly metadata workload at our scale.
    """

    def __init__(self, config: HuggingFaceIndexConfig) -> None:
        self._config = config
        self._api = HfApi(
            endpoint=config.huggingface.api_base,
            token=config.huggingface.token,
        )

    @property
    def api(self) -> HfApi:
        return self._api

    @property
    def token(self) -> str | None:
        return self._config.huggingface.token

    # ---- Listing ---------------------------------------------------------

    def list_models(self, author: str, *, limit: int | None = None) -> Iterable[Any]:
        try:
            return self._api.list_models(author=author, limit=limit, full=False)
        except HfHubHTTPError as exc:
            LOGGER.warning("list_models(%s) failed: %s", author, exc)
            return []

    def list_datasets(self, author: str, *, limit: int | None = None) -> Iterable[Any]:
        try:
            return self._api.list_datasets(author=author, limit=limit, full=False)
        except HfHubHTTPError as exc:
            LOGGER.warning("list_datasets(%s) failed: %s", author, exc)
            return []

    def list_spaces(self, author: str, *, limit: int | None = None) -> Iterable[Any]:
        try:
            return self._api.list_spaces(author=author, limit=limit, full=False)
        except HfHubHTTPError as exc:
            LOGGER.warning("list_spaces(%s) failed: %s", author, exc)
            return []

    def search_models(self, term: str, *, limit: int | None = None) -> Iterable[Any]:
        try:
            return self._api.list_models(search=term, limit=limit, full=False)
        except HfHubHTTPError as exc:
            LOGGER.warning("search_models(%s) failed: %s", term, exc)
            return []

    def search_datasets(self, term: str, *, limit: int | None = None) -> Iterable[Any]:
        try:
            return self._api.list_datasets(search=term, limit=limit, full=False)
        except HfHubHTTPError as exc:
            LOGGER.warning("search_datasets(%s) failed: %s", term, exc)
            return []

    # ---- Namespace overview ----------------------------------------------

    def namespace_overview(self, slug: str) -> tuple[str, Any] | None:
        """Return `(kind, overview)` for a namespace, or None if it doesn't exist.

        Tries the org endpoint first, falls back to the user endpoint on 404.
        `kind` is `'org'` or `'user'`.
        """
        try:
            return ("org", self._api.get_organization_overview(slug))
        except HfHubHTTPError as org_exc:
            if getattr(getattr(org_exc, "response", None), "status_code", None) != 404:
                LOGGER.warning("organization_overview(%s) HTTP error: %s", slug, org_exc)
        try:
            return ("user", self._api.get_user_overview(slug))
        except HfHubHTTPError as user_exc:
            if getattr(getattr(user_exc, "response", None), "status_code", None) != 404:
                LOGGER.warning("user_overview(%s) HTTP error: %s", slug, user_exc)
        return None

    # ---- Per-repo info ---------------------------------------------------

    def model_info(self, repo_id: str, *, expand: tuple[str, ...]) -> Any | None:
        return self._safe_info("model", repo_id, expand=expand)

    def dataset_info(self, repo_id: str, *, expand: tuple[str, ...]) -> Any | None:
        return self._safe_info("dataset", repo_id, expand=expand)

    def space_info(self, repo_id: str, *, expand: tuple[str, ...]) -> Any | None:
        return self._safe_info("space", repo_id, expand=expand)

    def _safe_info(
        self,
        kind: str,
        repo_id: str,
        *,
        expand: tuple[str, ...],
    ) -> Any | None:
        method = {
            "model": self._api.model_info,
            "dataset": self._api.dataset_info,
            "space": self._api.space_info,
        }[kind]
        try:
            return method(repo_id=repo_id, expand=list(expand))
        except (RepositoryNotFoundError, GatedRepoError) as exc:
            LOGGER.info("skipping %s %s: %s", kind, repo_id, exc.__class__.__name__)
            return None
        except HfHubHTTPError as exc:
            LOGGER.warning("%s_info(%s) HTTP error: %s", kind, repo_id, exc)
            return None

    # ---- README fetching --------------------------------------------------

    def fetch_readme(self, repo_id: str, *, repo_type: str) -> str | None:
        """Return the README markdown for a repo, or None if absent.

        Tries common aliases (`README.md`, `README`, `Readme.md`). Returns
        None on any HF error so the ingest can continue with the metadata
        it already has.
        """
        for filename in README_FILENAME_FALLBACKS:
            try:
                # Streaming download via hf_hub_download is fine here — the
                # file is tiny and the client caches by sha.
                from huggingface_hub import hf_hub_download
                local_path = hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    repo_type=repo_type,
                    token=self._config.huggingface.token,
                    endpoint=self._config.huggingface.api_base,
                )
                with open(local_path, encoding="utf-8") as fh:
                    return fh.read()
            except EntryNotFoundError:
                continue
            except (RepositoryNotFoundError, GatedRepoError):
                return None
            except HfHubHTTPError as exc:
                LOGGER.warning(
                    "fetch_readme(%s, %s) HTTP error: %s",
                    repo_id,
                    filename,
                    exc,
                )
                return None
        return None

    def snapshot_card_files(self, repo_id: str, *, repo_type: str, local_dir) -> None:
        """Pull all card-adjacent files (README/JSON/YAML/MD) into local_dir.

        Used only when `huggingface.full_cards` is True. Always ignores
        weight patterns.
        """
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=repo_id,
                repo_type=repo_type,
                allow_patterns=list(CARD_PATTERNS),
                ignore_patterns=list(WEIGHT_PATTERNS),
                local_dir=str(local_dir),
                token=self._config.huggingface.token,
                endpoint=self._config.huggingface.api_base,
            )
        except (RepositoryNotFoundError, GatedRepoError):
            return
        except HfHubHTTPError as exc:
            LOGGER.warning("snapshot_card_files(%s) HTTP error: %s", repo_id, exc)
