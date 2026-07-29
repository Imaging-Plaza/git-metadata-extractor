"""GME-maintained gimie-api sidecar.

Replaces ``ghcr.io/sdsc-ordes/gimie-api``: the published images (pinned digest
AND :latest) ship gimie 0.6.1 with an app written against the 0.7.x API —
every ``/gimie/ttl`` request dies on ``Project.serialize`` (AttributeError)
and the upstream error path returns the raw ``Exception`` object, which
FastAPI JSON-encodes into ``{}`` — a silently empty payload. See
``dev/split-rag-indices/11-gimie-sidecar-jsonld-broken.md``.

Extraction calls mirror the proven in-process reference
(``src/v1/gimie_utils/gimie_methods.py``): ``Project(url)`` →
``proj.extract()`` → ``graph.serialize(format=...)``.

Error contract (kept compatible with the old image + the GME client):
failures return **HTTP 200** with ``output`` set to the error *string* —
the client detects a non-parsing payload and degrades to ``None``.
"""
from __future__ import annotations

import logging
import os
from importlib.metadata import version

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gimie-api")


def _normalize_token_env() -> None:
    """Promote a single PAT into GITHUB_TOKEN (what gimie actually reads).

    gimie's GithubExtractor uses ``os.environ["GITHUB_TOKEN"]`` verbatim and
    401s when the value contains commas. Accept the legacy image's
    ``ACCESS_TOKEN`` too, and comma-separated pools from either var — the
    first token wins.
    """
    raw = os.environ.get("GITHUB_TOKEN") or os.environ.get("ACCESS_TOKEN") or ""
    first = next((t.strip() for t in raw.split(",") if t.strip()), "")
    if first:
        os.environ["GITHUB_TOKEN"] = first
    else:
        logger.warning("no GITHUB_TOKEN/ACCESS_TOKEN set — GitHub extraction will be rate-limited/fail")


_normalize_token_env()

from gimie.project import Project  # noqa: E402 — import after token normalization

GIMIE_VERSION = version("gimie")
app = FastAPI(title="gimie-api (GME-maintained)", version=GIMIE_VERSION)


@app.get("/")
def index() -> dict:
    return {"title": "gimie-api (GME-maintained)", "gimie": GIMIE_VERSION}


@app.get("/health")
def health() -> dict:
    return {"status": "healthy", "gimie": GIMIE_VERSION}


def _extract(full_path: str, fmt: str) -> str:
    proj = Project(full_path)
    graph = proj.extract()
    return graph.serialize(format=fmt)


@app.get("/gimie/ttl/{full_path:path}")
def gimie_ttl(full_path: str) -> dict:
    try:
        return {"link": full_path, "output": _extract(full_path, "ttl")}
    except Exception as exc:  # noqa: BLE001 — error contract: message string in output
        logger.exception("gimie ttl extraction failed for %s", full_path)
        return {"link": full_path, "output": f"Exception: {exc}"}


@app.get("/gimie/jsonld/{full_path:path}")
def gimie_jsonld(full_path: str) -> dict:
    try:
        return {"link": full_path, "output": _extract(full_path, "json-ld")}
    except Exception as exc:  # noqa: BLE001
        logger.exception("gimie jsonld extraction failed for %s", full_path)
        return {"link": full_path, "output": f"Exception: {exc}"}
