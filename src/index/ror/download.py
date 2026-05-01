"""Download + extract the latest ROR Zenodo dump.

Resolves the latest record under the concept DOI (default
`10.5281/zenodo.6347574`) via the Zenodo REST API, downloads the release zip
into `dump_dir()`, verifies the SHA256 checksum from the Zenodo file metadata,
and extracts the v2 JSON payload. Re-running is a no-op when the latest
release version is already cached locally.

Filename matching covers both:
  - `<release>-ror-data.json`               (v2.0+ — Dec 2025 onward, v2-only)
  - `<release>-ror-data_schema_v2.json`     (v1.45–v1.x — released alongside v1)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from .paths import dump_dir

logger = logging.getLogger(__name__)

ZENODO_API_BASE = "https://zenodo.org/api"
_V2_FILENAME_PATTERNS = (
    re.compile(r".*ror-data(?:_schema_v2)?\.json$", re.IGNORECASE),
)
_DEFAULT_TIMEOUT = 60
_DOWNLOAD_TIMEOUT = 600
_DOI_RE = re.compile(r"^(?P<prefix>10\.\d+)/(?P<suffix>.+)$")


class RorDumpError(RuntimeError):
    """Raised when the dump can't be located, downloaded, or extracted."""


@dataclass(frozen=True)
class CachedDump:
    release_version: str
    release_doi: Optional[str]
    json_path: Path


def _doi_to_concept_recid(concept_doi: str) -> str:
    """Convert `10.5281/zenodo.6347574` → `6347574`."""
    m = _DOI_RE.match(concept_doi.strip())
    if not m:
        msg = f"Unrecognized DOI: {concept_doi!r}"
        raise RorDumpError(msg)
    suffix = m.group("suffix")
    last = suffix.rsplit(".", 1)[-1]
    digits = "".join(ch for ch in last if ch.isdigit())
    if not digits:
        msg = f"Could not extract Zenodo recid from DOI suffix {suffix!r}"
        raise RorDumpError(msg)
    return digits


def _resolve_latest_record(concept_doi: str) -> dict:
    recid = _doi_to_concept_recid(concept_doi)
    url = f"{ZENODO_API_BASE}/records/{recid}/versions/latest"
    resp = requests.get(url, timeout=_DEFAULT_TIMEOUT)
    if resp.status_code != 200:
        msg = (
            f"Zenodo lookup failed for concept DOI {concept_doi!r}: "
            f"HTTP {resp.status_code}"
        )
        raise RorDumpError(msg)
    return resp.json()


def _pick_zip_file(record: dict) -> dict:
    files = record.get("files") or []
    for entry in files:
        key = entry.get("key", "")
        if isinstance(key, str) and key.endswith(".zip"):
            return entry
    msg = "No .zip asset found in Zenodo record."
    raise RorDumpError(msg)


def _release_version(record: dict) -> str:
    metadata = record.get("metadata") or {}
    version = metadata.get("version")
    if isinstance(version, str) and version:
        return version
    title = metadata.get("title")
    if isinstance(title, str) and title:
        return title
    return str(record.get("id", "unknown"))


def _release_doi(record: dict) -> Optional[str]:
    doi = record.get("doi")
    return doi if isinstance(doi, str) else None


def _release_dir(version: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", version)
    p = dump_dir() / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


def _existing_v2_json(release_path: Path) -> Optional[Path]:
    for child in release_path.iterdir():
        if not child.is_file():
            continue
        for pattern in _V2_FILENAME_PATTERNS:
            if pattern.match(child.name):
                return child
    return None


def _verify_sha256(path: Path, expected: str) -> None:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    if digest != expected:
        msg = (
            f"Checksum mismatch for {path.name}: "
            f"expected {expected}, got {digest}"
        )
        raise RorDumpError(msg)


def _stream_download(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as f:
            shutil.copyfileobj(resp.raw, f)
        tmp.replace(dest)


def _extract_v2_json(zip_path: Path, into: Path) -> Path:
    with zipfile.ZipFile(zip_path) as zf:
        candidate: Optional[zipfile.ZipInfo] = None
        for info in zf.infolist():
            base = Path(info.filename).name
            for pattern in _V2_FILENAME_PATTERNS:
                if pattern.match(base):
                    candidate = info
                    break
            if candidate is not None:
                break
        if candidate is None:
            msg = (
                f"No v2 JSON file found inside {zip_path.name}. "
                f"Looked for *-ror-data.json or *-ror-data_schema_v2.json."
            )
            raise RorDumpError(msg)
        out_path = into / Path(candidate.filename).name
        with zf.open(candidate) as src, out_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return out_path


def fetch_latest_dump(
    concept_doi: str = "10.5281/zenodo.6347574",
    *,
    refresh: bool = False,
) -> CachedDump:
    """Resolve, download (if needed), and extract the latest ROR v2 JSON."""
    record = _resolve_latest_record(concept_doi)
    version = _release_version(record)
    release_dir = _release_dir(version)

    cached = _existing_v2_json(release_dir)
    if cached is not None and not refresh:
        logger.info("Using cached ROR dump version=%s at %s", version, cached)
        return CachedDump(
            release_version=version,
            release_doi=_release_doi(record),
            json_path=cached,
        )

    file_entry = _pick_zip_file(record)
    download_url = file_entry["links"]["self"]
    zip_path = release_dir / file_entry["key"]

    logger.info("Downloading ROR dump version=%s from %s", version, download_url)
    _stream_download(download_url, zip_path)

    expected_checksum = file_entry.get("checksum")
    if isinstance(expected_checksum, str) and expected_checksum.startswith("md5:"):
        # Zenodo emits md5 by default; sha256 is opt-in. Skip when not sha256.
        logger.debug("Zenodo file checksum is md5; skipping sha256 verify.")
    elif isinstance(expected_checksum, str) and expected_checksum.startswith("sha256:"):
        _verify_sha256(zip_path, expected_checksum.split(":", 1)[1])

    json_path = _extract_v2_json(zip_path, release_dir)
    metadata_path = release_dir / "release.json"
    metadata_path.write_text(
        json.dumps(
            {
                "version": version,
                "doi": _release_doi(record),
                "concept_doi": concept_doi,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return CachedDump(
        release_version=version,
        release_doi=_release_doi(record),
        json_path=json_path,
    )
