"""python -m git_metadata_extractor.experiments {list,validate,benchmark}."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from git_metadata_extractor.experiments.benchmark import BenchmarkCase, run_benchmark
from git_metadata_extractor.experiments.builtins import default_catalog
from git_metadata_extractor.experiments.models import load_profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List registered built-in components")
    validate = commands.add_parser(
        "validate", help="Validate a profile without provider/model calls",
    )
    validate.add_argument("profile", type=Path)
    benchmark = commands.add_parser(
        "benchmark", help="Run full extraction trials locally",
    )
    benchmark.add_argument("--profile", type=Path, action="append", required=True)
    benchmark.add_argument("--corpus", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.add_argument("--providers", choices=["mock", "live"], default="mock")
    benchmark.add_argument(
        "--cache-mode", choices=["cold", "warm_pipeline"], default="cold",
    )
    benchmark.add_argument("--repeats", type=int, default=1)
    benchmark.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    catalog = default_catalog()
    try:
        if args.command == "list":
            print(
                json.dumps(
                    {
                        "collectors": list(catalog.collectors),
                    "agents": list(catalog.agents),
                    "tool_catalog": catalog.tools.manifest(),
                    },
                    indent=2,
                ),
            )
        elif args.command == "validate":
            print(json.dumps(catalog.manifest(load_profile(args.profile)), indent=2))
        else:
            corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
            report = asyncio.run(
                run_benchmark(
                    [load_profile(p) for p in args.profile],
                    [BenchmarkCase.model_validate(c) for c in corpus["cases"]],
                    output_dir=args.output,
                    catalog=catalog,
                    repeats=args.repeats,
                    provider_mode=args.providers,
                    cache_mode=args.cache_mode,
                    timeout_seconds=args.timeout,
                ),
            )
            print(json.dumps(report["profiles"], indent=2))
            return int(any(row["status"] != "completed" for row in report["trials"]))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(
            2,
            f"Configuration/artifact error ({type(exc).__name__}); validate profile fields and use a new output directory.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
