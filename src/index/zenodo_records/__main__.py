"""Entry point: `python -m src.index.zenodo_records`."""

from __future__ import annotations

from src.index.zenodo_records.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
