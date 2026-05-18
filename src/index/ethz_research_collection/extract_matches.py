"""Extract matches stage: regex GitHub / HuggingFace URLs from fetched text.

Output: `matches.jsonl`, one `MatchRecord` per item that contains at least
one matched URL. Reuses `classify_github_url` for GitHub canonicalisation
and adds a small HuggingFace classifier alongside.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from src.v2.ingest.detection.github_url_classifier import classify_github_url

from .config import EthzResearchCollectionIndexConfig
from .models import MatchRecord
from .paths import matches_path, text_dir

logger = logging.getLogger(__name__)

_URL_RE = re.compile(
    r"https?://[^\s<>\"'\)\]\}]+",
    re.IGNORECASE,
)

_HF_HOSTS = {"huggingface.co", "hf.co"}
_GH_HOSTS = {"github.com", "www.github.com"}


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def classify_huggingface_url(url: str) -> Optional[Tuple[str, str]]:
    """Return (kind, canonical_url) for a HuggingFace URL, or None.

    `kind` is one of: 'model', 'dataset', 'space', 'org', 'user', 'other'.
    """
    host = _hostname(url)
    if host not in _HF_HOSTS:
        return None
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts:
        return ("other", url)
    head = parts[0].lower()
    if head == "datasets" and len(parts) >= 2:
        canonical = "https://huggingface.co/datasets/" + "/".join(parts[1:3])
        return ("dataset", canonical)
    if head == "spaces" and len(parts) >= 2:
        canonical = "https://huggingface.co/spaces/" + "/".join(parts[1:3])
        return ("space", canonical)
    if head in {"organizations", "orgs"} and len(parts) >= 2:
        return ("org", f"https://huggingface.co/{parts[1]}")
    # Bare path: user/model or just user.
    if len(parts) == 1:
        return ("user", f"https://huggingface.co/{parts[0]}")
    canonical = f"https://huggingface.co/{parts[0]}/{parts[1]}"
    return ("model", canonical)


def _canonicalise(url: str) -> Optional[str]:
    """Return a canonical URL string if the URL points at GitHub or HF, else None.

    GitHub URLs that classify_github_url rejects (issues, blobs, etc.) are
    kept as-is — they're still valid evidence of GitHub references.
    """
    host = _hostname(url)
    if host in _GH_HOSTS:
        try:
            result = classify_github_url(url)
            return result.normalized_url
        except Exception:
            return url
    if host in _HF_HOSTS:
        hf = classify_huggingface_url(url)
        return hf[1] if hf else None
    return None


def extract_matches_single(uuid: str) -> Optional[MatchRecord]:
    """Run match extraction for one UUID. Reads ``text/<uuid>.txt`` and
    returns a :class:`MatchRecord` if any GitHub/HF URL is found, else
    ``None``. Does NOT touch the shared ``matches.jsonl`` aggregate; the
    caller decides whether to persist.
    """
    text_file = text_dir() / f"{uuid}.txt"
    if not text_file.exists():
        return None
    text = text_file.read_text(encoding="utf-8", errors="replace")
    urls, counts = _extract_from_text(text)
    if not urls:
        return None
    return MatchRecord(
        uuid=uuid,
        matched_urls=sorted(urls),
        counts_by_host=dict(counts),
    )


def _extract_from_text(text: str) -> Tuple[Set[str], Counter]:
    found: Set[str] = set()
    counts: Counter = Counter()
    for raw in _URL_RE.findall(text):
        # Trim trailing punctuation that often clings to URLs in prose.
        clean = raw.rstrip(".,;:'\"()[]{}<>")
        canonical = _canonicalise(clean)
        if canonical is None:
            continue
        found.add(canonical)
        counts[_hostname(canonical)] += 1
    return found, counts


def extract_matches(_cfg: EthzResearchCollectionIndexConfig) -> dict:
    """Walk text/ files, write matches.jsonl. Returns counts dict."""
    out_path = matches_path()
    text_files = sorted(text_dir().glob("*.txt"))
    if not text_files:
        logger.warning("No text files. Run `fetch-text` first.")
        out_path.write_text("", encoding="utf-8")
        return {"items": 0, "with_matches": 0}

    written = 0
    with out_path.open("w", encoding="utf-8") as out:
        for tf in text_files:
            uuid = tf.stem
            text = tf.read_text(encoding="utf-8", errors="replace")
            urls, counts = _extract_from_text(text)
            if not urls:
                continue
            record = MatchRecord(
                uuid=uuid,
                matched_urls=sorted(urls),
                counts_by_host=dict(counts),
            )
            out.write(record.model_dump_json() + "\n")
            written += 1

    logger.info(
        "extract_matches: items=%d with_matches=%d → %s",
        len(text_files), written, out_path,
    )
    return {
        "items": len(text_files),
        "with_matches": written,
        "matches_path": str(out_path),
    }


def load_matches() -> List[MatchRecord]:
    """Read all match records from disk."""
    path = matches_path()
    if not path.exists():
        return []
    out: List[MatchRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(MatchRecord(**json.loads(line)))
    return out


def matches_by_uuid() -> Dict[str, MatchRecord]:
    return {m.uuid: m for m in load_matches()}


def run(cfg: EthzResearchCollectionIndexConfig) -> dict:
    return extract_matches(cfg)
