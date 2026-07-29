"""Docker Compose detection + image/tag extraction.

- `parse_compose_images` / `_resolve_compose_image` (pure)
- `_is_compose_path` (provider helper)
- `RealGitHubProvider.get_repository_compose_files` (mocked tree + contents)
- repository agent stamping `_compose_files` / `_compose_file_count` /
  `_compose_images`.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any
from unittest.mock import patch

from git_metadata_extractor.agents import ProviderSet, RepositoryAgentV2
from git_metadata_extractor.agents.rule_based._repo_signals import (
    _image_ref_to_url,
    _resolve_compose_image,
    compose_image_urls,
    parse_compose_images,
)
from git_metadata_extractor.providers.github_provider import (
    RealGitHubProvider,
    _is_compose_path,
)
from git_metadata_extractor.providers.mock_github import MockGitHubProvider

_EXPECTED_IMAGES = 2

_COMPOSE = (
    "services:\n"
    "  db:\n"
    "    image: qdrant/qdrant:latest\n"
    "  api:\n"
    "    image: ${API_IMAGE:-ghcr.io/acme/api@sha256:abc}\n"
    "  builder:\n"
    "    build: .\n"            # no image → skipped
    "  novar:\n"
    "    image: ${UNRESOLVED}\n"  # unresolvable → skipped
)


class _Resp:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


def _provider() -> RealGitHubProvider:
    return RealGitHubProvider(
        gimie_extractor=lambda _u, _f: {},
        user_lookup=lambda u: {"login": u},
        organization_lookup=lambda o: {"login": o},
    )


# ---------------------------------------------------------------------------
# _is_compose_path
# ---------------------------------------------------------------------------


def test_is_compose_path() -> None:
    assert _is_compose_path("docker-compose.yml")
    assert _is_compose_path(".devcontainer/docker-compose.yml")
    assert _is_compose_path("docker/compose.prod.yaml")
    assert _is_compose_path("compose.yaml")
    assert not _is_compose_path("README.md")
    assert not _is_compose_path("config/compose-notes.txt")
    assert not _is_compose_path("src/composer.json")


# ---------------------------------------------------------------------------
# _resolve_compose_image / parse_compose_images
# ---------------------------------------------------------------------------


def test_resolve_compose_image() -> None:
    assert _resolve_compose_image("qdrant/qdrant:latest") == "qdrant/qdrant:latest"
    assert _resolve_compose_image("${V:-ghcr.io/a/b@sha256:x}") == "ghcr.io/a/b@sha256:x"
    assert _resolve_compose_image("${NOIMG}") is None
    assert _resolve_compose_image(None) is None
    assert _resolve_compose_image("   ") is None


def test_parse_compose_images() -> None:
    images = parse_compose_images([{"content": _COMPOSE}])
    assert images == ["qdrant/qdrant:latest", "ghcr.io/acme/api@sha256:abc"]
    assert len(images) == _EXPECTED_IMAGES


def test_parse_compose_images_dedupes_and_handles_bad_input() -> None:
    files = [
        {"content": "services:\n  a:\n    image: x:1\n"},
        {"content": "services:\n  b:\n    image: x:1\n"},  # dup
        {"content": "not: valid: yaml: ["},                 # unparseable
        {"content": "no services here"},
        "not a dict",
    ]
    assert parse_compose_images(files) == ["x:1"]
    assert parse_compose_images(None) == []


# ---------------------------------------------------------------------------
# _image_ref_to_url / compose_image_urls
# ---------------------------------------------------------------------------


def test_image_ref_to_url() -> None:
    # tag appended where the registry supports a per-tag page
    assert _image_ref_to_url("qdrant/qdrant:latest") == (
        "https://hub.docker.com/r/qdrant/qdrant/tags?name=latest"
    )
    assert _image_ref_to_url("postgres:15") == (
        "https://hub.docker.com/_/postgres/tags?name=15"  # official + tag
    )
    assert _image_ref_to_url("python") == "https://hub.docker.com/_/python"  # no tag
    assert _image_ref_to_url("docker.io/acme/api:1") == (
        "https://hub.docker.com/r/acme/api/tags?name=1"
    )
    assert _image_ref_to_url("quay.io/org/name:2") == (
        "https://quay.io/repository/org/name?tab=tags&tag=2"
    )
    # GHCR has no clean per-tag page → base package page (digest carries no tag)
    assert _image_ref_to_url("ghcr.io/acme/api@sha256:x") == (
        "https://github.com/acme/api/pkgs/container/api"
    )
    # unknown registry / localhost → no clean web URL
    assert _image_ref_to_url("registry.gitlab.com/x/y:1") is None
    assert _image_ref_to_url("localhost:5000/foo:dev") is None
    assert _image_ref_to_url(None) is None


def test_compose_image_urls() -> None:
    compose = (
        "services:\n"
        "  a:\n    image: qdrant/qdrant:latest\n"
        "  b:\n    image: ghcr.io/acme/api@sha256:x\n"
        "  c:\n    image: registry.gitlab.com/x/y:1\n"   # dropped (unknown)
    )
    assert compose_image_urls([{"content": compose}]) == [
        "https://hub.docker.com/r/qdrant/qdrant/tags?name=latest",
        "https://github.com/acme/api/pkgs/container/api",
    ]
    assert compose_image_urls(None) == []


# ---------------------------------------------------------------------------
# provider.get_repository_compose_files (mocked tree + contents)
# ---------------------------------------------------------------------------


def _b64(text: str) -> dict[str, Any]:
    return {"content": base64.b64encode(text.encode()).decode()}


def test_get_repository_compose_files() -> None:
    tree = _Resp(200, {"tree": [
        {"type": "blob", "path": ".devcontainer/docker-compose.yml"},
        {"type": "blob", "path": "README.md"},
        {"type": "tree", "path": "docker"},
    ]})
    content = _Resp(200, _b64(_COMPOSE))
    with patch(
        "git_metadata_extractor.providers.github_provider.requests.get",
        side_effect=[tree, content],
    ):
        out = _provider().get_repository_compose_files("acme/tool")
    assert len(out) == 1
    assert out[0]["path"] == ".devcontainer/docker-compose.yml"
    assert out[0]["html_url"] == (
        "https://github.com/acme/tool/blob/HEAD/.devcontainer/docker-compose.yml"
    )
    assert "qdrant/qdrant:latest" in out[0]["content"]


def test_get_repository_compose_files_none_on_non_200() -> None:
    with patch(
        "git_metadata_extractor.providers.github_provider.requests.get",
        return_value=_Resp(404, None),
    ):
        assert _provider().get_repository_compose_files("a/b") == []


# ---------------------------------------------------------------------------
# repository agent emission
# ---------------------------------------------------------------------------


def _stub_context(*, compose_files: list[dict[str, Any]] | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": "tool", "full_name": "acme/tool",
        "owner": {"login": "acme", "type": "Organization"},
        "created_at": "2023-01-01T00:00:00Z", "license": {"spdx_id": "MIT"},
        "fork": False, "source": {"full_name": None},
    }
    if compose_files is not None:
        metadata["compose_files"] = compose_files
    return {
        "full_name": "acme/tool", "metadata": metadata,
        "readme_content": "", "contributors": [{"login": "alice"}],
        "languages": {"Python": 1}, "aux_files": {},
    }


def _run(context: dict[str, Any]) -> dict[str, Any]:
    result = asyncio.run(
        RepositoryAgentV2().run(
            {"full_name": "acme/tool", "repository_context": context},
            ProviderSet(github=MockGitHubProvider()),
        ),
    )
    return result.raw_output


def test_agent_emits_compose_fields() -> None:
    raw = _run(_stub_context(compose_files=[
        {
            "path": ".devcontainer/docker-compose.yml",
            "html_url": "https://github.com/acme/tool/blob/HEAD/.devcontainer/docker-compose.yml",
            "content": _COMPOSE,
        },
    ]))
    assert raw["_compose_files"] == [
        "https://github.com/acme/tool/blob/HEAD/.devcontainer/docker-compose.yml",
    ]
    assert raw["_compose_file_count"] == 1
    assert raw["_compose_images"] == ["qdrant/qdrant:latest", "ghcr.io/acme/api@sha256:abc"]
    assert raw["_compose_image_urls"] == [
        "https://hub.docker.com/r/qdrant/qdrant/tags?name=latest",
        "https://github.com/acme/api/pkgs/container/api",
    ]


def test_agent_compose_fields_none_when_absent() -> None:
    raw = _run(_stub_context(compose_files=None))
    assert raw["_compose_files"] is None
    assert raw["_compose_file_count"] is None
    assert raw["_compose_images"] is None
    assert raw["_compose_image_urls"] is None
