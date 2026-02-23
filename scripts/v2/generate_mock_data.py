from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.v2.testing.mock_generator import (  # noqa: E402
    DEFAULT_ARTICLE_COUNT,
    DEFAULT_GITHUB_ORGANIZATION_COUNT,
    DEFAULT_ORGANIZATION_COUNT,
    DEFAULT_PERSON_COUNT,
    DEFAULT_REPOSITORY_COUNT,
    DEFAULT_SEED,
    generate_dataset,
    write_dataset,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic v2 mock data")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--persons", type=int, default=DEFAULT_PERSON_COUNT)
    parser.add_argument("--repos", type=int, default=DEFAULT_REPOSITORY_COUNT)
    parser.add_argument("--orgs", type=int, default=DEFAULT_ORGANIZATION_COUNT)
    parser.add_argument("--github-orgs", type=int, default=DEFAULT_GITHUB_ORGANIZATION_COUNT)
    parser.add_argument("--articles", type=int, default=DEFAULT_ARTICLE_COUNT)
    parser.add_argument("--edge-cases", action="store_true", default=False)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory where pulse_* JSON files will be written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = generate_dataset(
        seed=args.seed,
        persons=args.persons,
        repos=args.repos,
        orgs=args.orgs,
        github_orgs=args.github_orgs,
        articles=args.articles,
        edge_cases=args.edge_cases,
    )
    write_dataset(dataset, output_dir=args.output)


if __name__ == "__main__":
    main()
