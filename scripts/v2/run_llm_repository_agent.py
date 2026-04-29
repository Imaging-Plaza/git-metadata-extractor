# ruff: noqa: INP001, T201
"""Run the LLM repository agent end-to-end against a real GitHub repository.

Usage:
    just v2-run-repo-agent sdsc-ordes/gimie
    python scripts/v2/run_llm_repository_agent.py sdsc-ordes/gimie
    python scripts/v2/run_llm_repository_agent.py https://github.com/sdsc-ordes/gimie
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from src.v2.agents.llm.repository import LLMRepositoryAgentV2
from src.v2.agents.models import ProviderSet
from src.v2.dependencies import _default_provider_set
from src.v2.ingest.detection.github_url_classifier import classify_github_url
from src.v2.pipeline.stages import gather_context

_SEP = "─" * 60


def _pj(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


async def _run(repo: str) -> None:
    if "/" in repo and not repo.startswith("http") and "github.com" not in repo:
        repo = f"github.com/{repo}"
    url_info = classify_github_url(repo)
    full_name = f"{url_info.owner}/{url_info.repo}"

    print(f"\n{_SEP}")
    print(f"  Repository : {full_name}")
    print(f"{_SEP}\n")

    print("[ 1 / 2 ]  Gathering GIMIE context …\n")
    providers: ProviderSet = _default_provider_set(use_mock_providers=False)
    bundle = await gather_context("repository", url_info, providers)

    repo_ctx = bundle.context["repository"]
    readme = repo_ctx.get("readme_content") or ""

    print(f"Metadata:\n{_pj(repo_ctx.get('metadata', {}))}\n")
    print(f"Contributors:\n{_pj(repo_ctx.get('contributors', []))}\n")
    print(f"Languages:\n{_pj(repo_ctx.get('languages', {}))}\n")
    print(f"README ({len(readme)} chars):\n{readme[:500]}{'…' if len(readme) > 500 else ''}\n")
    if bundle.warnings:
        print(f"Context warnings: {bundle.warnings}\n")

    print(f"{_SEP}")
    print("[ 2 / 2 ]  Running LLM repository agent …\n")

    agent = LLMRepositoryAgentV2()
    context = {
        "full_name": full_name,
        "source_url": url_info.normalized_url,
        "repository_context": repo_ctx,
    }
    result = await agent.run(context, providers)

    print(f"Model    : {result.model}")
    print(f"Provider : {result.provider}")
    print(f"Tokens   : prompt={result.tokens_prompt}  completion={result.tokens_completion}")
    if result.warnings:
        print(f"\nWarnings:")
        for w in result.warnings:
            print(f"  • {w}")
    print(f"\nAgent output:\n{_pj(result.data)}")
    print(f"\n{_SEP}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LLM repository agent with real GIMIE context.")
    parser.add_argument("repo", help="GitHub repo as owner/repo or full URL")
    args = parser.parse_args()
    asyncio.run(_run(args.repo))


if __name__ == "__main__":
    main()
