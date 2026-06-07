"""Tests for README badge parsing + conda / crates.io / RubyGems discovery.

Extends the npm/PyPI machinery (see ``test_npm_pypi_packages.py``). No real
network anywhere — a fake session / fake provider is injected.

Covers:
  - parse_badges: linked + plain-image badges, dedupe, empty.
  - extract_registry_coords: pypi/npm/conda/crates/rubygems via link AND
    via shields/fury image; CI badge ignored; conda channel+name tuple.
  - parse_crates_name: cargo.toml [package].name, missing/malformed.
  - PackageRegistryProvider.get_{conda,crates,rubygems}_package: canned JSON
    → thin dict (versions, repository_url, conda channel, crates
    max_stable_version); 404 → None; crates User-Agent header asserted.
  - context_gather: README with CSBDeep-style badges → badges populated,
    conda discovered + verified (dev_url back-ref), mismatch dropped, crates
    from cargo.toml.
  - repository_agent: emits `_badges`/`_badge_count` + `_conda_*`/`_crates_*`/
    `_rubygems_*` (+ `_conda_channel`); all-None when absent.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src.v2.agents import ProviderSet, RepositoryAgentV2
from src.v2.agents.rule_based._repo_signals import (
    extract_registry_coords,
    parse_badges,
    parse_crates_name,
)
from src.v2.ingest.providers.mock_github import MockGitHubProvider
from src.v2.ingest.providers.package_registry_provider import (
    PackageRegistryProvider,
)
from src.v2.pipeline.stages.context_gather import (
    _enrich_repository_metadata_with_registry_packages,
)

# Expected counts (named to satisfy the magic-value lint).
_CSBDEEP_BADGE_COUNT = 3
_BADGE_CAP = 100
_AGENT_BADGE_COUNT = 2

# ---------------------------------------------------------------------------
# Fakes — no real network anywhere in this module.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


class FakeSession:
    """A `requests`-like session returning canned responses keyed by URL."""

    def __init__(self, routes: dict[str, _FakeResponse]) -> None:
        self._routes = routes
        self.requested: list[str] = []
        self.headers_seen: list[dict[str, str] | None] = []

    def get(
        self,
        url: str,
        timeout: float | None = None,  # noqa: ARG002
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.requested.append(url)
        self.headers_seen.append(headers)
        return self._routes.get(url, _FakeResponse(404, None))


class FakeMultiRegistryProvider:
    """A fake `package_registry` provider returning canned thin dicts."""

    def __init__(
        self,
        *,
        npm: dict[str, Any] | None = None,
        pypi: dict[str, Any] | None = None,
        conda: dict[str, Any] | None = None,
        crates: dict[str, Any] | None = None,
        rubygems: dict[str, Any] | None = None,
    ) -> None:
        self._npm = npm
        self._pypi = pypi
        self._conda = conda
        self._crates = crates
        self._rubygems = rubygems
        self.npm_calls: list[str] = []
        self.pypi_calls: list[str] = []
        self.conda_calls: list[tuple[str, str]] = []
        self.crates_calls: list[str] = []
        self.rubygems_calls: list[str] = []

    def get_npm_package(self, name: str) -> dict[str, Any] | None:
        self.npm_calls.append(name)
        return dict(self._npm) if isinstance(self._npm, dict) else None

    def get_pypi_package(self, name: str) -> dict[str, Any] | None:
        self.pypi_calls.append(name)
        return dict(self._pypi) if isinstance(self._pypi, dict) else None

    def get_conda_package(self, channel: str, name: str) -> dict[str, Any] | None:
        self.conda_calls.append((channel, name))
        return dict(self._conda) if isinstance(self._conda, dict) else None

    def get_crates_package(self, name: str) -> dict[str, Any] | None:
        self.crates_calls.append(name)
        return dict(self._crates) if isinstance(self._crates, dict) else None

    def get_rubygems_package(self, name: str) -> dict[str, Any] | None:
        self.rubygems_calls.append(name)
        return dict(self._rubygems) if isinstance(self._rubygems, dict) else None


# ---------------------------------------------------------------------------
# parse_badges
# ---------------------------------------------------------------------------


def test_parse_badges_linked_and_plain() -> None:
    readme = (
        "# Project\n"
        "[![PyPI](https://img.shields.io/pypi/v/foo)](https://pypi.org/project/foo)\n"
        "![Build](https://img.shields.io/badge/build-passing-green)\n"
    )
    badges = parse_badges(readme)
    assert badges == [
        {
            "label": "PyPI",
            "image_url": "https://img.shields.io/pypi/v/foo",
            "link_url": "https://pypi.org/project/foo",
        },
        {
            "label": "Build",
            "image_url": "https://img.shields.io/badge/build-passing-green",
            "link_url": None,
        },
    ]


def test_parse_badges_dedupe() -> None:
    readme = (
        "![A](https://img.shields.io/x)\n"
        "![A](https://img.shields.io/x)\n"
    )
    badges = parse_badges(readme)
    assert len(badges) == 1


def test_parse_badges_linked_not_double_counted() -> None:
    # The inner ![alt](img) of a linked badge must not produce a second
    # plain-image record.
    readme = "[![A](https://img.shields.io/x)](https://example.com)"
    badges = parse_badges(readme)
    assert len(badges) == 1
    assert badges[0]["link_url"] == "https://example.com"


def test_parse_badges_empty_and_none() -> None:
    assert parse_badges(None) == []
    assert parse_badges("") == []
    assert parse_badges("no badges here") == []


def test_parse_badges_cap() -> None:
    readme = "\n".join(
        f"![b{i}](https://img.shields.io/{i})" for i in range(150)
    )
    badges = parse_badges(readme)
    assert len(badges) == _BADGE_CAP


# ---------------------------------------------------------------------------
# extract_registry_coords
# ---------------------------------------------------------------------------


def _coord(image: str, link: str | None = None) -> list[dict[str, Any]]:
    return [{"label": "x", "image_url": image, "link_url": link}]


def test_extract_coords_pypi_link_and_image() -> None:
    assert extract_registry_coords(
        _coord("img", "https://pypi.org/project/mypkg"),
    ) == {"pypi": "mypkg"}
    assert extract_registry_coords(
        _coord("https://badge.fury.io/py/mypkg"),
    ) == {"pypi": "mypkg"}
    assert extract_registry_coords(
        _coord("https://img.shields.io/pypi/v/mypkg"),
    ) == {"pypi": "mypkg"}


def test_extract_coords_strips_badge_image_extension() -> None:
    # Image-only badges (badge.fury.io / shields with an explicit .svg) must
    # NOT leak the extension into the package name.
    assert extract_registry_coords(
        _coord("https://badge.fury.io/py/csbdeep.svg"),
    ) == {"pypi": "csbdeep"}
    assert extract_registry_coords(
        _coord("https://img.shields.io/crates/v/serde.svg"),
    ) == {"crates": "serde"}
    assert extract_registry_coords(
        _coord("https://badge.fury.io/rb/rails.svg"),
    ) == {"rubygems": "rails"}
    assert extract_registry_coords(
        _coord("https://img.shields.io/conda/v/conda-forge/csbdeep.svg"),
    ) == {"conda": ("conda-forge", "csbdeep")}


def test_extract_coords_npm_link_and_image_scoped() -> None:
    assert extract_registry_coords(
        _coord("img", "https://www.npmjs.com/package/@scope/pkg"),
    ) == {"npm": "@scope/pkg"}
    assert extract_registry_coords(
        _coord("https://img.shields.io/npm/v/lodash"),
    ) == {"npm": "lodash"}
    assert extract_registry_coords(
        _coord("https://badge.fury.io/js/lodash"),
    ) == {"npm": "lodash"}


def test_extract_coords_conda_tuple_link_and_image() -> None:
    assert extract_registry_coords(
        _coord("img", "https://anaconda.org/conda-forge/csbdeep"),
    ) == {"conda": ("conda-forge", "csbdeep")}
    assert extract_registry_coords(
        _coord("https://img.shields.io/conda/v/bioconda/mypkg"),
    ) == {"conda": ("bioconda", "mypkg")}
    # Anaconda badge image with /badges/ suffix — channel+name only.
    assert extract_registry_coords(
        _coord("https://anaconda.org/conda-forge/csbdeep/badges/version.svg"),
    ) == {"conda": ("conda-forge", "csbdeep")}


def test_extract_coords_crates_link_and_image() -> None:
    assert extract_registry_coords(
        _coord("img", "https://crates.io/crates/serde"),
    ) == {"crates": "serde"}
    assert extract_registry_coords(
        _coord("https://img.shields.io/crates/v/serde"),
    ) == {"crates": "serde"}


def test_extract_coords_rubygems_link_and_image() -> None:
    assert extract_registry_coords(
        _coord("img", "https://rubygems.org/gems/rails"),
    ) == {"rubygems": "rails"}
    assert extract_registry_coords(
        _coord("https://img.shields.io/gem/v/rails"),
    ) == {"rubygems": "rails"}
    assert extract_registry_coords(
        _coord("https://badge.fury.io/rb/rails"),
    ) == {"rubygems": "rails"}


def test_extract_coords_ci_badge_ignored() -> None:
    badges = _coord(
        "https://github.com/o/r/workflows/CI/badge.svg",
        "https://github.com/o/r/actions",
    )
    assert extract_registry_coords(badges) == {}


def test_extract_coords_first_match_per_ecosystem() -> None:
    badges = [
        {"label": "a", "image_url": "https://img.shields.io/pypi/v/first", "link_url": None},
        {"label": "b", "image_url": "https://img.shields.io/pypi/v/second", "link_url": None},
    ]
    assert extract_registry_coords(badges)["pypi"] == "first"


def test_extract_coords_url_decode() -> None:
    assert extract_registry_coords(
        _coord("img", "https://www.npmjs.com/package/%40scope%2Fpkg"),
    ) == {"npm": "@scope/pkg"}


def test_extract_coords_empty() -> None:
    assert extract_registry_coords([]) == {}
    assert extract_registry_coords(None) == {}


# ---------------------------------------------------------------------------
# parse_crates_name
# ---------------------------------------------------------------------------


def test_parse_crates_name_valid() -> None:
    aux = {"cargo.toml": '[package]\nname = "my-crate"\nversion = "0.1.0"\n'}
    assert parse_crates_name(aux) == "my-crate"


def test_parse_crates_name_case_insensitive_filename() -> None:
    aux = {"Cargo.toml": '[package]\nname = "my-crate"\n'}
    assert parse_crates_name(aux) == "my-crate"


def test_parse_crates_name_missing() -> None:
    assert parse_crates_name({"package.json": "{}"}) is None
    assert parse_crates_name({}) is None
    assert parse_crates_name(None) is None


def test_parse_crates_name_malformed() -> None:
    assert parse_crates_name({"cargo.toml": "[package\nname = broken"}) is None


def test_parse_crates_name_no_name() -> None:
    assert parse_crates_name({"cargo.toml": '[package]\nversion = "0.1.0"\n'}) is None


# ---------------------------------------------------------------------------
# PackageRegistryProvider — conda / crates / rubygems via fake session
# ---------------------------------------------------------------------------

_CONDA_JSON = {
    "name": "csbdeep",
    "latest_version": "0.7.4",
    "versions": ["0.6.0", "0.7.0", "0.7.4"],
    "dev_url": "https://github.com/CSBDeep/CSBDeep",
    "source_git_url": "https://github.com/other/mirror",
    "html_url": "https://anaconda.org/conda-forge/csbdeep",
    "files": [
        {"upload_time": "2023-01-01T00:00:00"},
        {"upload_time": "2023-06-01T00:00:00"},
    ],
}

_CRATES_JSON = {
    "crate": {
        "name": "serde",
        "max_stable_version": "1.0.197",
        "newest_version": "1.0.198-beta",
        "repository": "https://github.com/serde-rs/serde",
        "updated_at": "2024-03-01T10:00:00Z",
    },
    "versions": [
        {"num": "1.0.197"},
        {"num": "1.0.196"},
    ],
}

_RUBYGEMS_JSON = {
    "name": "rails",
    "version": "7.1.0",
    "source_code_uri": "https://github.com/rails/rails",
    "homepage_uri": "https://rubyonrails.org",
}

_RUBYGEMS_VERSIONS_JSON = [
    {"number": "7.1.0", "created_at": "2024-01-01T00:00:00.000Z"},
    {"number": "7.0.0", "created_at": "2023-01-01T00:00:00.000Z"},
]


def test_provider_get_conda_package_thin_dict() -> None:
    session = FakeSession({
        "https://api.anaconda.org/package/conda-forge/csbdeep": _FakeResponse(
            200, _CONDA_JSON,
        ),
    })
    pkg = PackageRegistryProvider(session=session).get_conda_package(
        "conda-forge", "csbdeep",
    )
    assert pkg is not None
    assert pkg["name"] == "csbdeep"
    assert pkg["latest_version"] == "0.7.4"
    assert pkg["versions"] == ["0.6.0", "0.7.0", "0.7.4"]
    assert pkg["repository_url"] == "https://github.com/CSBDeep/CSBDeep"
    assert pkg["registry_url"] == "https://anaconda.org/conda-forge/csbdeep"
    assert pkg["channel"] == "conda-forge"
    assert pkg["latest_release_date"] == "2023-06-01T00:00:00"


def test_provider_get_conda_package_404_returns_none() -> None:
    session = FakeSession({})  # everything 404s
    assert PackageRegistryProvider(session=session).get_conda_package(
        "conda-forge", "missing",
    ) is None


def test_provider_get_crates_package_thin_dict() -> None:
    session = FakeSession({
        "https://crates.io/api/v1/crates/serde": _FakeResponse(200, _CRATES_JSON),
    })
    pkg = PackageRegistryProvider(session=session).get_crates_package("serde")
    assert pkg is not None
    assert pkg["name"] == "serde"
    # max_stable_version wins over newest_version (which is a beta).
    assert pkg["latest_version"] == "1.0.197"
    assert pkg["versions"] == ["1.0.197", "1.0.196"]
    assert pkg["repository_url"] == "https://github.com/serde-rs/serde"
    assert pkg["latest_release_date"] == "2024-03-01T10:00:00Z"
    assert pkg["registry_url"] == "https://crates.io/crates/serde"


def test_provider_get_crates_user_agent_header_present() -> None:
    session = FakeSession({
        "https://crates.io/api/v1/crates/serde": _FakeResponse(200, _CRATES_JSON),
    })
    PackageRegistryProvider(session=session).get_crates_package("serde")
    assert session.headers_seen
    for headers in session.headers_seen:
        assert headers is not None
        assert "User-Agent" in headers
        assert headers["User-Agent"].strip()


def test_provider_get_crates_package_404_returns_none() -> None:
    session = FakeSession({})
    assert PackageRegistryProvider(session=session).get_crates_package(
        "nope",
    ) is None


def test_provider_get_rubygems_package_thin_dict() -> None:
    session = FakeSession({
        "https://rubygems.org/api/v1/gems/rails.json": _FakeResponse(
            200, _RUBYGEMS_JSON,
        ),
        "https://rubygems.org/api/v1/versions/rails.json": _FakeResponse(
            200, _RUBYGEMS_VERSIONS_JSON,
        ),
    })
    pkg = PackageRegistryProvider(session=session).get_rubygems_package("rails")
    assert pkg is not None
    assert pkg["name"] == "rails"
    assert pkg["latest_version"] == "7.1.0"
    assert pkg["versions"] == ["7.1.0", "7.0.0"]
    assert pkg["repository_url"] == "https://github.com/rails/rails"
    assert pkg["registry_url"] == "https://rubygems.org/gems/rails"
    assert pkg["latest_release_date"] == "2024-01-01T00:00:00.000Z"


def test_provider_get_rubygems_versions_failure_tolerated() -> None:
    # Primary gem JSON succeeds, versions endpoint 404s → versions None,
    # but the package is still returned.
    session = FakeSession({
        "https://rubygems.org/api/v1/gems/rails.json": _FakeResponse(
            200, _RUBYGEMS_JSON,
        ),
    })
    pkg = PackageRegistryProvider(session=session).get_rubygems_package("rails")
    assert pkg is not None
    assert pkg["versions"] is None
    assert pkg["latest_release_date"] is None
    assert pkg["latest_version"] == "7.1.0"


def test_provider_get_rubygems_homepage_fallback() -> None:
    payload = {
        "name": "homeonly",
        "version": "1.0.0",
        "homepage_uri": "https://github.com/acme/homeonly",
    }
    session = FakeSession({
        "https://rubygems.org/api/v1/gems/homeonly.json": _FakeResponse(
            200, payload,
        ),
    })
    pkg = PackageRegistryProvider(session=session).get_rubygems_package(
        "homeonly",
    )
    assert pkg is not None
    assert pkg["repository_url"] == "https://github.com/acme/homeonly"


def test_provider_get_rubygems_404_returns_none() -> None:
    session = FakeSession({})
    assert PackageRegistryProvider(session=session).get_rubygems_package(
        "nope",
    ) is None


# ---------------------------------------------------------------------------
# context_gather — badges + multi-registry discovery + link policy
# ---------------------------------------------------------------------------

# CSBDeep-style README: PyPI + conda badges + a CI badge.
_CSBDEEP_README = (
    "# CSBDeep\n"
    "[![PyPI version]"
    "(https://img.shields.io/pypi/v/csbdeep)]"
    "(https://pypi.org/project/csbdeep)\n"
    "[![Anaconda-Server Badge]"
    "(https://anaconda.org/conda-forge/csbdeep/badges/version.svg)]"
    "(https://anaconda.org/conda-forge/csbdeep)\n"
    "[![CI]"
    "(https://github.com/CSBDeep/CSBDeep/workflows/CI/badge.svg)]"
    "(https://github.com/CSBDeep/CSBDeep/actions)\n"
)


def _run_enrich(
    *,
    full_name: str,
    aux_files: dict[str, str],
    provider: Any,
    readme: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    providers = ProviderSet(github=MockGitHubProvider(), package_registry=provider)
    _enrich_repository_metadata_with_registry_packages(
        full_name=full_name,
        aux_files=aux_files,
        repository_metadata=metadata,
        providers=providers,
        warnings=warnings,
        readme=readme,
    )
    return metadata, warnings


def test_context_gather_badges_populated() -> None:
    provider = FakeMultiRegistryProvider()
    metadata, _ = _run_enrich(
        full_name="CSBDeep/CSBDeep",
        aux_files={},
        provider=provider,
        readme=_CSBDEEP_README,
    )
    assert isinstance(metadata["badges"], list)
    assert len(metadata["badges"]) == _CSBDEEP_BADGE_COUNT
    labels = [b["label"] for b in metadata["badges"]]
    assert "PyPI version" in labels


def test_context_gather_conda_verified_via_dev_url() -> None:
    provider = FakeMultiRegistryProvider(
        conda={
            "name": "csbdeep",
            "channel": "conda-forge",
            "repository_url": "https://github.com/CSBDeep/CSBDeep",
        },
    )
    metadata, _ = _run_enrich(
        full_name="CSBDeep/CSBDeep",
        aux_files={},
        provider=provider,
        readme=_CSBDEEP_README,
    )
    assert provider.conda_calls == [("conda-forge", "csbdeep")]
    assert metadata["conda_package"]["link"] == "verified"
    assert metadata["conda_package"]["channel"] == "conda-forge"


def test_context_gather_conda_mismatch_dropped() -> None:
    provider = FakeMultiRegistryProvider(
        conda={
            "name": "csbdeep",
            "channel": "conda-forge",
            "repository_url": "https://github.com/someone-else/other",
        },
    )
    metadata, _ = _run_enrich(
        full_name="CSBDeep/CSBDeep",
        aux_files={},
        provider=provider,
        readme=_CSBDEEP_README,
    )
    assert "conda_package" not in metadata


def test_context_gather_crates_from_cargo_toml() -> None:
    provider = FakeMultiRegistryProvider(
        crates={
            "name": "my-crate",
            "repository_url": "https://github.com/acme/my-crate",
        },
    )
    metadata, _ = _run_enrich(
        full_name="acme/my-crate",
        aux_files={"cargo.toml": '[package]\nname = "my-crate"\n'},
        provider=provider,
        readme=None,
    )
    assert provider.crates_calls == ["my-crate"]
    assert metadata["crates_package"]["link"] == "verified"


def test_context_gather_crates_from_badge_when_no_manifest() -> None:
    provider = FakeMultiRegistryProvider(
        crates={"name": "serde", "repository_url": None},
    )
    readme = "[![crate](https://img.shields.io/crates/v/serde)](https://crates.io/crates/serde)"
    metadata, _ = _run_enrich(
        full_name="serde-rs/serde",
        aux_files={},
        provider=provider,
        readme=readme,
    )
    assert provider.crates_calls == ["serde"]
    assert metadata["crates_package"]["link"] == "name_only"


def test_context_gather_rubygems_from_badge() -> None:
    provider = FakeMultiRegistryProvider(
        rubygems={
            "name": "rails",
            "repository_url": "https://github.com/rails/rails",
        },
    )
    readme = "[![gem](https://img.shields.io/gem/v/rails)](https://rubygems.org/gems/rails)"
    metadata, _ = _run_enrich(
        full_name="rails/rails",
        aux_files={},
        provider=provider,
        readme=readme,
    )
    assert provider.rubygems_calls == ["rails"]
    assert metadata["rubygems_package"]["link"] == "verified"


def test_context_gather_npm_falls_back_to_badge_coords() -> None:
    # No package.json manifest → npm name comes from the badge.
    provider = FakeMultiRegistryProvider(
        npm={"name": "lodash", "repository_url": None},
    )
    readme = "[![npm](https://img.shields.io/npm/v/lodash)](https://www.npmjs.com/package/lodash)"
    metadata, _ = _run_enrich(
        full_name="lodash/lodash",
        aux_files={},
        provider=provider,
        readme=readme,
    )
    assert provider.npm_calls == ["lodash"]
    assert metadata["npm_package"]["link"] == "name_only"


def test_context_gather_badges_parsed_without_provider() -> None:
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    providers = ProviderSet(github=MockGitHubProvider())  # no package_registry
    _enrich_repository_metadata_with_registry_packages(
        full_name="CSBDeep/CSBDeep",
        aux_files={},
        repository_metadata=metadata,
        providers=providers,
        warnings=warnings,
        readme=_CSBDEEP_README,
    )
    # Badges still parsed even though registry lookups are skipped.
    assert len(metadata["badges"]) == _CSBDEEP_BADGE_COUNT
    assert "conda_package" not in metadata
    assert warnings == []


# ---------------------------------------------------------------------------
# repository_agent — flat scalar emission
# ---------------------------------------------------------------------------


def _make_repo_context(
    *,
    badges: list[dict[str, Any]] | None = None,
    conda_package: dict[str, Any] | None = None,
    crates_package: dict[str, Any] | None = None,
    rubygems_package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": "tool",
        "full_name": "acme/tool",
        "owner": {"login": "acme", "type": "Organization"},
        "created_at": "2023-01-01T00:00:00Z",
        "license": {"spdx_id": "MIT"},
        "fork": False,
        "source": {"full_name": None},
    }
    if badges is not None:
        metadata["badges"] = badges
    if conda_package is not None:
        metadata["conda_package"] = conda_package
    if crates_package is not None:
        metadata["crates_package"] = crates_package
    if rubygems_package is not None:
        metadata["rubygems_package"] = rubygems_package
    return {
        "full_name": "acme/tool",
        "metadata": metadata,
        "readme_content": "",
        "contributors": [{"login": "alice"}],
        "languages": {"Python": 1},
        "aux_files": {},
    }


def test_repository_agent_emits_badges_and_multi_registry() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    badges = [
        {"label": "PyPI", "image_url": "https://img.shields.io/pypi/v/x", "link_url": None},
        {"label": "conda", "image_url": "https://anaconda.org/conda-forge/x/badges/v.svg", "link_url": None},
    ]
    conda = {
        "name": "csbdeep",
        "channel": "conda-forge",
        "latest_version": "0.7.4",
        "versions": ["0.7.0", "0.7.4"],
        "latest_release_date": "2023-06-01T00:00:00",
        "registry_url": "https://anaconda.org/conda-forge/csbdeep",
        "link": "verified",
    }
    crates = {
        "name": "serde",
        "latest_version": "1.0.197",
        "versions": ["1.0.196", "1.0.197"],
        "latest_release_date": "2024-03-01T10:00:00Z",
        "registry_url": "https://crates.io/crates/serde",
        "link": "verified",
    }
    rubygems = {
        "name": "rails",
        "latest_version": "7.1.0",
        "versions": ["7.0.0", "7.1.0"],
        "latest_release_date": "2024-01-01T00:00:00Z",
        "registry_url": "https://rubygems.org/gems/rails",
        "link": "name_only",
    }
    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_repo_context(
                    badges=badges,
                    conda_package=conda,
                    crates_package=crates,
                    rubygems_package=rubygems,
                ),
            },
            providers,
        ),
    )
    raw = result.raw_output
    assert raw["_badges"] == badges
    assert raw["_badge_count"] == _AGENT_BADGE_COUNT
    assert raw["_conda_package"] == "csbdeep"
    assert raw["_conda_latest_version"] == "0.7.4"
    assert raw["_conda_channel"] == "conda-forge"
    assert raw["_conda_link"] == "verified"
    assert raw["_crates_package"] == "serde"
    assert raw["_crates_latest_version"] == "1.0.197"
    assert raw["_crates_link"] == "verified"
    assert raw["_rubygems_package"] == "rails"
    assert raw["_rubygems_latest_version"] == "7.1.0"
    assert raw["_rubygems_link"] == "name_only"


def test_repository_agent_multi_registry_all_none_when_absent() -> None:
    agent = RepositoryAgentV2()
    providers = ProviderSet(github=MockGitHubProvider())
    result = asyncio.run(
        agent.run(
            {
                "full_name": "acme/tool",
                "repository_context": _make_repo_context(),
            },
            providers,
        ),
    )
    raw = result.raw_output
    assert raw["_badges"] is None
    assert raw["_badge_count"] is None
    assert raw["_conda_channel"] is None
    for prefix in ("_conda_", "_crates_", "_rubygems_"):
        for suffix in (
            "package",
            "latest_version",
            "versions",
            "latest_release_date",
            "registry_url",
            "link",
        ):
            assert raw[prefix + suffix] is None, prefix + suffix
