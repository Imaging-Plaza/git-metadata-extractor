from __future__ import annotations

from importlib.resources import files


def load_prompt(package: str, filename: str) -> str:
    """Load a prompt template from a package data file."""

    return files(package).joinpath(filename).read_text(encoding="utf-8")
