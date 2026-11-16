"""Shared V2 client and command-line options for the enrichment examples."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx

from git_metadata_extractor.api_models import V2ExtractResponse

Runtime = Literal["rule_based", "hybrid", "llm"]


def extract(
    client: httpx.Client,
    source_url: str,
    *,
    runtime: Runtime = "rule_based",
    include_run_report: bool = False,
) -> V2ExtractResponse:
    """Extract and validate the response with the service's Pydantic contract."""
    response = client.get(
        f"v2/extract/{quote(source_url, safe=':/')}",
        params={
            "output_format": "jsonld",
            "agent_runtime": runtime,
            "include_run_report": str(include_run_report).lower(),
        },
    )
    response.raise_for_status()
    return V2ExtractResponse.model_validate(response.json())


def main(default_url: str, output_name: str) -> None:
    """Run a synchronous extraction against an already configured server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_url", nargs="?", default=default_url)
    parser.add_argument(
        "--runtime",
        choices=("rule_based", "hybrid", "llm"),
        default="rule_based",
    )
    parser.add_argument("--run-report", action="store_true")
    parser.add_argument(
        "--api-url",
        default=os.getenv("GME_API_URL", "http://localhost:1234"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/examples") / output_name,
    )
    args = parser.parse_args()
    token = os.getenv("API_TOKEN")
    if not token:
        parser.error("Set API_TOKEN to the token configured on the server.")
    with httpx.Client(
        base_url=args.api_url.rstrip("/") + "/",
        headers={"Authorization": f"Bearer {token}"},
        timeout=600,
    ) as client:
        result = extract(
            client,
            args.source_url,
            runtime=args.runtime,
            include_run_report=args.run_report,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        result.model_dump_json(indent=2, by_alias=True, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Saved {result.stats.entities_count} entities and response metadata to {args.output}",
    )
    for warning in result.warnings:
        print(f"Warning: {warning}")
