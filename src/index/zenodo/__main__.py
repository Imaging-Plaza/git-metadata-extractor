"""Entry point: `python -m src.index.zenodo`."""

from __future__ import annotations

from src.index.zenodo.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
