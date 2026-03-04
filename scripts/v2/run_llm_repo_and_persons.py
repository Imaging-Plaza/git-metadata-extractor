# ruff: noqa: INP001, T201
"""Run LLM repository agent + LLM person agents end-to-end against a real GitHub repo.

Executes the first three stages of the repository pipeline:
  1. context_gather  — gather GIMIE context
  2. repo_agent      — LLM repository extraction
  3. person_agents   — LLM person extraction for each contributor (concurrent)

Usage:
    just v2-run-repo-and-persons sdsc-ordes/gimie
    python scripts/v2/run_llm_repo_and_persons.py sdsc-ordes/gimie
    python scripts/v2/run_llm_repo_and_persons.py https://github.com/sdsc-ordes/gimie
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from src.v2.agents.llm.person import LLMPersonAgentV2
from src.v2.agents.llm.repository import LLMRepositoryAgentV2
from src.v2.agents.models import AgentResult, ProviderSet
from src.v2.dependencies import _default_provider_set
from src.v2.detection.github_url_classifier import classify_github_url
from src.v2.pipeline.stages import gather_context

_SEP = "─" * 60
_SEP_THIN = "·" * 60


@dataclass(slots=True)
class _PersonExecutionOutcome:
    username: str
    status: Literal["ok", "timeout", "error"]
    elapsed_seconds: float
    result: AgentResult | None = None
    error: str | None = None


def _pj(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _contributor_usernames(repository_context: dict) -> list[str]:
    """Extract unique contributor logins, excluding organization accounts."""
    contributors = repository_context.get("contributors", [])
    seen: set[str] = set()
    logins: list[str] = []
    for c in contributors:
        if isinstance(c, dict):
            if str(c.get("type", "")).lower() == "organization":
                continue
            login = c.get("login")
        elif isinstance(c, str):
            login = c
        else:
            continue
        if isinstance(login, str) and login.strip() and login not in seen:
            seen.add(login)
            logins.append(login.strip())
    return logins


def _classify_person_error(error: BaseException) -> Literal["timeout", "error"]:
    if "timed out" in str(error).lower():
        return "timeout"
    return "error"


def _positive_float(raw_value: str) -> float:
    parsed = float(raw_value)
    if parsed <= 0:
        message = "value must be > 0"
        raise argparse.ArgumentTypeError(message)
    return parsed


def _positive_int(raw_value: str) -> int:
    parsed = int(raw_value)
    if parsed <= 0:
        message = "value must be > 0"
        raise argparse.ArgumentTypeError(message)
    return parsed


async def _run(
    repo: str,
    *,
    person_timeout_seconds: float,
    max_concurrency: int,
    heartbeat_seconds: float,
) -> None:
    if "/" in repo and not repo.startswith("http") and "github.com" not in repo:
        repo = f"github.com/{repo}"
    url_info = classify_github_url(repo)
    full_name = f"{url_info.owner}/{url_info.repo}"

    print(f"\n{_SEP}")
    print(f"  Repository : {full_name}")
    print(f"{_SEP}\n")

    providers: ProviderSet = _default_provider_set(use_mock_providers=False)

    # ── Stage 1: context_gather ──────────────────────────────────────────────
    print("[ 1 / 3 ]  Gathering GIMIE context …\n")
    bundle = await gather_context("repository", url_info, providers)
    repo_ctx = bundle.context.get("repository", {})

    readme = repo_ctx.get("readme_content") or ""
    print(f"Metadata:\n{_pj(repo_ctx.get('metadata', {}))}\n")
    print(f"Contributors:\n{_pj(repo_ctx.get('contributors', []))}\n")
    print(f"Languages:\n{_pj(repo_ctx.get('languages', {}))}\n")
    print(f"README ({len(readme)} chars): {readme[:300]}{'…' if len(readme) > 300 else ''}\n")
    if bundle.warnings:
        print(f"Context warnings: {bundle.warnings}\n")

    # ── Stage 2: repo_agent ──────────────────────────────────────────────────
    print(f"{_SEP}")
    print("[ 2 / 3 ]  Running LLM repository agent …\n")

    repo_agent = LLMRepositoryAgentV2()
    repo_result = await repo_agent.run(
        {
            "full_name": full_name,
            "source_url": url_info.normalized_url,
            "repository_context": repo_ctx,
        },
        providers,
    )

    print(f"Model    : {repo_result.model}")
    print(f"Provider : {repo_result.provider}")
    print(f"Tokens   : prompt={repo_result.tokens_prompt}  completion={repo_result.tokens_completion}")
    if repo_result.warnings:
        print("Warnings:")
        for w in repo_result.warnings:
            print(f"  • {w}")
    print(f"\nRepository entity:\n{_pj(repo_result.data)}\n")

    # ── Stage 3: person_agents (concurrent) ─────────────────────────────────
    usernames = _contributor_usernames(repo_ctx)
    print(f"{_SEP}")
    print(f"[ 3 / 3 ]  Running LLM person agent for {len(usernames)} contributor(s): {usernames}\n")

    if not usernames:
        print("  No contributors found — skipping person stage.\n")
        return

    person_agent = LLMPersonAgentV2(
        llm_call_timeout_seconds=person_timeout_seconds,
    )
    source_repositories = [full_name]
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _run_person(username: str) -> _PersonExecutionOutcome:
        async with semaphore:
            print(f"  → {username}: starting …")
            t0 = perf_counter()
            ctx = {
                "username": username,
                "source_url": url_info.normalized_url,
                "source_repositories": source_repositories,
                # Share repository context so the LLM can scan the README for
                # ORCID identifiers, affiliations, and author credits.
                "repository_context": repo_ctx,
            }
            try:
                result = await person_agent.run(ctx, providers)
            except Exception as exc:  # noqa: BLE001
                elapsed_seconds = perf_counter() - t0
                status = _classify_person_error(exc)
                print(
                    f"  ✗ {username}: {status} in {elapsed_seconds:.1f}s ({exc})",
                )
                return _PersonExecutionOutcome(
                    username=username,
                    status=status,
                    elapsed_seconds=elapsed_seconds,
                    error=str(exc),
                )

            elapsed_seconds = perf_counter() - t0
            print(f"  ✓ {username}: done in {elapsed_seconds:.1f}s")
            return _PersonExecutionOutcome(
                username=username,
                status="ok",
                elapsed_seconds=elapsed_seconds,
                result=result,
            )

    outcomes_by_username: dict[str, _PersonExecutionOutcome] = {}
    task_to_username: dict[asyncio.Task[_PersonExecutionOutcome], str] = {
        asyncio.create_task(_run_person(username)): username for username in usernames
    }
    pending_tasks = set(task_to_username)
    fanout_started_at = perf_counter()

    while pending_tasks:
        done, pending_tasks = await asyncio.wait(
            pending_tasks,
            timeout=heartbeat_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            running = sorted(task_to_username[task] for task in pending_tasks)
            print(
                "  … heartbeat: "
                f"{len(running)} contributor(s) still running after {perf_counter() - fanout_started_at:.1f}s: "
                f"{running}",
            )
            continue

        for task in done:
            username = task_to_username[task]
            try:
                outcome = task.result()
            except Exception as exc:  # noqa: BLE001
                status = _classify_person_error(exc)
                outcome = _PersonExecutionOutcome(
                    username=username,
                    status=status,
                    elapsed_seconds=perf_counter() - fanout_started_at,
                    error=str(exc),
                )
            outcomes_by_username[username] = outcome

    ordered_outcomes = [outcomes_by_username[username] for username in usernames]
    for outcome in ordered_outcomes:
        print(_SEP_THIN)
        if outcome.status != "ok" or outcome.result is None:
            print(f"  Person: {outcome.username}")
            print(f"  Status   : {outcome.status}")
            print(f"  Error    : {outcome.error}\n")
            continue
        result = outcome.result
        print(f"  Person: {outcome.username}")
        print(f"  Model    : {result.model}")
        print(f"  Tokens   : prompt={result.tokens_prompt}  completion={result.tokens_completion}")
        if result.warnings:
            print("  Warnings:")
            for w in result.warnings:
                print(f"    • {w}")
        print(f"  Entity:\n{_pj(result.data)}\n")

    successful_person_results = [
        outcome.result.data
        for outcome in ordered_outcomes
        if outcome.status == "ok" and outcome.result is not None
    ]
    timeout_count = sum(1 for outcome in ordered_outcomes if outcome.status == "timeout")
    error_count = sum(1 for outcome in ordered_outcomes if outcome.status == "error")

    print(_SEP_THIN)
    print(
        "Person summary: "
        f"ok={len(successful_person_results)} timeout={timeout_count} error={error_count}",
    )
    failed_outcomes = [outcome for outcome in ordered_outcomes if outcome.status != "ok"]
    if failed_outcomes:
        print("Failed contributors:")
        for outcome in failed_outcomes:
            print(f"  - {outcome.username}: {outcome.status} ({outcome.error})")
    print("\nPerson entities:")
    print(_pj(successful_person_results))

    print(_SEP)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run LLM repo + person agents for the first three pipeline stages.",
    )
    parser.add_argument("repo", help="GitHub repo as owner/repo or full URL")
    parser.add_argument(
        "--person-timeout-seconds",
        type=_positive_float,
        default=180.0,
        help="Per-person timeout for LLM person extraction (seconds).",
    )
    parser.add_argument(
        "--max-concurrency",
        type=_positive_int,
        default=3,
        help="Maximum number of concurrent person agent calls.",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=_positive_float,
        default=15.0,
        help="How often to print fanout heartbeat while waiting for person tasks.",
    )
    args = parser.parse_args()
    asyncio.run(
        _run(
            args.repo,
            person_timeout_seconds=args.person_timeout_seconds,
            max_concurrency=args.max_concurrency,
            heartbeat_seconds=args.heartbeat_seconds,
        ),
    )


if __name__ == "__main__":
    main()
