"""The extractor must import and run without the sources implementation."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = {"open_pulse_sources", "duckdb", "qdrant_client", "faiss", "graphai_client"}


def test_runtime_has_no_index_implementation_imports():
    violations = []
    for path in (ROOT / "git_metadata_extractor").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module or ""]
            violations.extend(
                f"{path.relative_to(ROOT)}:{node.lineno}: {module}"
                for module in modules
                if module.split(".")[0] in FORBIDDEN
            )
    assert not violations, "\n".join(violations)


def test_installed_dependencies_do_not_include_index_implementation():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dependencies = project["project"]["dependencies"]
    forbidden = {name.replace("_", "-") for name in FORBIDDEN} | {"faiss-cpu"}
    for requirement in dependencies:
        name = requirement.split(" ")[0].split("=")[0].split(">")[0].split("<")[0]
        assert name.replace("_", "-") not in forbidden, requirement


def test_api_imports_without_sources_or_index_libraries():
    script = """
import importlib.abc
import sys
class BlockIndexLibraries(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'open_pulse_sources', 'duckdb', 'qdrant_client', 'faiss', 'graphai_client'}:
            raise ImportError('Extractor tried to import index implementation: ' + fullname)
sys.meta_path.insert(0, BlockIndexLibraries())
from git_metadata_extractor.app import app
assert app is not None
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter/script, no shell
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_index_configs_are_owned_by_the_sources_checkout():
    assert not list((ROOT / "config/index").glob("*"))


def test_deployment_gives_sources_exclusive_index_storage():
    stack = yaml.safe_load((ROOT / "tools/deploy/docker-compose.yml").read_text())
    api = stack["services"]["gme-api"]
    sources = stack["services"]["gme-sources"]
    assert api["environment"]["SOURCES_API_URL"] == "http://gme-sources:8080"
    assert "INDEX_DATA_DIR" not in api["environment"]
    assert "INDEX_QDRANT_URL" not in api["environment"]
    assert not set(api["volumes"]) & set(sources["volumes"])
    assert sources["build"]["context"] == "../../dev/open-pulse-sources"
    assert sources["environment"]["WORKERS"] == "1"
