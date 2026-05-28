"""Module entrypoint: delegates to the CLI."""

from __future__ import annotations

from src.index.orcid.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
