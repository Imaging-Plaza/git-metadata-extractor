"""Module entrypoint: ``python -m src.index.oamonitor <subcommand>``."""

from __future__ import annotations

from src.index.oamonitor.cli import main


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
