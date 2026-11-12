"""Exercise two built images without host ports, persistent volumes or upstream calls."""

# Standalone CLI; /tmp paths below exist only inside disposable containers.
# ruff: noqa: INP001, S108

from __future__ import annotations

import argparse
import subprocess
import uuid
from typing import TYPE_CHECKING

from sources_checkout import ROOT, load_lock

if TYPE_CHECKING:
    from pathlib import Path


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 - argv only, no shell
        ["docker", *args],  # noqa: S607 - developer's Docker on PATH
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return (result.stdout + result.stderr).strip()


def mount(path: Path, target: str) -> str:
    return f"type=bind,source={path},target={target},readonly"


def smoke(extractor_image: str, sources_image: str) -> None:
    lock = load_lock()
    network = "gme-boundary-smoke-" + uuid.uuid4().hex[:12]
    created = []
    fixtures = ROOT / "tests/contracts/docker"
    docker("network", "create", "--internal", network)
    try:
        for role, image in (("sources", sources_image), ("extractor", extractor_image)):
            name = f"{network}-{role}"
            created.append(name)
            command = [
                "run",
                "-d",
                "--network",
                network,
                "--network-alias",
                role,
                "--name",
                name,
                "-e",
                "API_TOKEN=smoke-only",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
            ]
            if role == "sources":
                command += [
                    "-e",
                    "INDEX_DATA_DIR=/tmp/index",
                    "--entrypoint",
                    "python",
                    "--mount",
                    mount(fixtures / "source_server.py", "/tmp/server.py"),
                    image,
                    "/tmp/server.py",
                ]
            else:
                command += [
                    "--mount",
                    mount(ROOT / "tests/v2/fixtures", "/app/tests/v2/fixtures"),
                    "-e",
                    "SOURCES_API_URL=http://sources:8080",
                    "-e",
                    "SOURCES_API_TOKEN=smoke-only",
                    "-e",
                    "V2_USE_MOCK_PROVIDERS=true",
                    "-e",
                    "V2_ROR_RAG_ENABLED=false",
                    "-e",
                    "V2_EPFL_GRAPH_RAG_ENABLED=false",
                    "-e",
                    "V2_CONCEPT_TAGGING_ENABLED=false",
                    "-e",
                    "WORKERS=1",
                    image,
                ]
            docker(*command)
        client = f"{network}-client"
        created.append(client)
        print(
            docker(
                "run",
                "--rm",
                "--name",
                client,
                "--network",
                network,
                "--entrypoint",
                "python",
                "-e",
                f"EXPECTED_SOURCES_VERSION={lock['version']}",
                "--mount",
                mount(fixtures / "client.py", "/tmp/client.py"),
                extractor_image,
                "/tmp/client.py",
            ),
        )
    except Exception:
        for name in created:
            print(docker("logs", "--tail", "40", name, check=False))
        raise
    finally:
        # These exact names belong to this invocation; no user's services/volumes.
        for name in reversed(created):
            docker("rm", "-f", name, check=False)
        docker("network", "rm", network, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extractor-image", default="git-metadata-extractor:local")
    parser.add_argument("--sources-image", default="open-pulse-sources:local")
    args = parser.parse_args()
    smoke(args.extractor_image, args.sources_image)
