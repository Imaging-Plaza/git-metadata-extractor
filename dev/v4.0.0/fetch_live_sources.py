"""Capture real data from the live sources GME reads, for the conversion test.

Writes small JSON files to dev/v4.0.0/examples/sources/ so the instance builder
is reproducible offline afterwards. Every file records the request URL and the
fetch time, mirroring the .meta.json sidecars of the committed snapshots — that
provenance becomes pulse:retrievedFrom / pulse:retrievedAt.

Run:  python dev/v4.0.0/fetch_live_sources.py
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent / "examples" / "sources"
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "git-metadata-extractor/3.0.0 (ontology conversion test)"}


def grab(name: str, url: str, params: dict | None = None) -> None:
    try:
        r = requests.get(url, params=params or {}, headers=UA, timeout=60)
        payload = {
            "request": {"url": r.url},
            "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status_code": r.status_code,
            "response": r.json() if r.status_code == 200 else None,
        }
        (OUT / f"{name}.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
        n = len(json.dumps(payload["response"] or ""))
        print(f"  {name:34} {r.status_code}  {n:>8} bytes")
    except Exception as e:                                    # noqa: BLE001
        print(f"  {name:34} FAILED {type(e).__name__}: {e}")


print("deps.dev — the package layer")
grab("depsdev_pypi_gimie", "https://api.deps.dev/v3alpha/systems/pypi/packages/gimie")
grab("depsdev_pypi_gimie_v040",
     "https://api.deps.dev/v3alpha/systems/pypi/packages/gimie/versions/0.4.0")
grab("depsdev_pypi_gimie_deps",
     "https://api.deps.dev/v3alpha/systems/pypi/packages/gimie/versions/0.4.0:dependencies")

print("ecosyste.ms — repositories, owners, funding")
grab("ecosystems_repo_gimie", "https://repos.ecosyste.ms/api/v1/repositories/lookup",
     {"url": "https://github.com/sdsc-ordes/gimie"})
grab("ecosystems_pkg_gimie", "https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/gimie")

print("OpenAlex — institutions")
grab("openalex_epfl", "https://api.openalex.org/institutions/ror:02s376052",
     {"mailto": "openpulse@epfl.ch"})

print("HuggingFace — the Hub")
grab("hf_models_sdsc", "https://huggingface.co/api/models", {"author": "SDSC", "limit": 3})

print("Docker Hub")
grab("dockerhub_sdsc", "https://hub.docker.com/v2/repositories/sdscordes/", {"page_size": 3})

print(f"\nwrote to {OUT}")
