"""Import contract with the open_pulse_sources library (GME task brief 04).

The extraction service imports the index layer's retrieval modules directly
— including lazy imports inside function bodies, where an ``ImportError``
is typically swallowed by degrade-to-``None`` guards. That combination hid
a dead HuggingFace RAG provider for weeks (the ``index.huggingface.config``
catch-all was retired upstream in ``9a5aa2b`` while this repo kept
importing it).

This test walks every ``.py`` file in the package with AST, collects every
``open_pulse_sources`` module (and imported symbol) referenced anywhere —
top-level or nested — and resolves each one against the installed library.
Any unresolvable path fails loudly with its source location, so a library
upgrade or refactor can never silently disable a provider again.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "git_metadata_extractor"


def _collect_references() -> list[tuple[str, str, str | None]]:
    """Return (source_location, module, symbol_or_None) for every reference."""
    refs: list[tuple[str, str, str | None]] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(PACKAGE_ROOT.parent).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("open_pulse_sources"):
                        refs.append((f"{rel}:{node.lineno}", alias.name, None))
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module and node.module.startswith("open_pulse_sources"):
                    for alias in node.names:
                        refs.append((f"{rel}:{node.lineno}", node.module, alias.name))
    return refs


REFERENCES = _collect_references()


def test_references_were_collected():
    # Sanity: the AST sweep must see the known-import-heavy providers.
    assert len(REFERENCES) > 20, REFERENCES


def test_every_open_pulse_sources_import_resolves():
    failures: list[str] = []
    for location, module, symbol in REFERENCES:
        try:
            mod = importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 — report, don't abort the sweep
            failures.append(f"{location}: import {module} failed: {exc}")
            continue
        if symbol is not None and symbol != "*" and not hasattr(mod, symbol):
            # `from pkg import name` also succeeds when `name` is a submodule.
            try:
                importlib.import_module(f"{module}.{symbol}")
            except ImportError:
                failures.append(f"{location}: {module} has no attribute {symbol!r}")
    assert not failures, (
        f"{len(failures)} unresolvable open_pulse_sources reference(s) — these "
        "silently disable their feature at runtime:\n" + "\n".join(failures)
    )


def test_huggingface_rag_provider_constructs():
    """Regression for the silently-dead HF provider (task brief 04/11).

    ``build_default_provider`` degrades to ``None`` on ANY failure — which
    hid the retired-config import for weeks. With the repo config tree
    present and the flag enabled it must construct.
    """
    pytest.importorskip("qdrant_client")
    import os

    from git_metadata_extractor.providers.huggingface_rag import (
        build_default_provider,
    )

    os.environ.setdefault("RCP_TOKEN", "contract-test-dummy")
    os.environ["V2_HUGGINGFACE_RAG_ENABLED"] = "true"
    try:
        provider = build_default_provider()
        assert provider is not None, (
            "HuggingFace RAG provider failed to construct — check the "
            "config import chain (see module docstring)"
        )
    finally:
        os.environ.pop("V2_HUGGINGFACE_RAG_ENABLED", None)
