"""Entry point: `python -m src.index.github_repos`."""

from __future__ import annotations

from src.index.github_repos.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
