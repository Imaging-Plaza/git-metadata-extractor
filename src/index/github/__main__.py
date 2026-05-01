"""Entry point: `python -m src.index.github`."""

from __future__ import annotations

from src.index.github.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
