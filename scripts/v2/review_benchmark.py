# ruff: noqa: INP001
"""Review an existing benchmark offline: python scripts/v2/review_benchmark.py DIR."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from git_metadata_extractor.experiments.quality import review_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    report = review_benchmark(args.directory)
    print(dict(Counter(trial.status for trial in report.trials)))
    print(args.directory / "quality.md")


if __name__ == "__main__":
    main()
