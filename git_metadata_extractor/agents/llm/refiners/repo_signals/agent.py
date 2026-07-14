"""LLM refiner that extracts documentation URLs and coverage fallback from a README.

Produces two ``gme-internal:`` repository fields:

- ``gme-internal:hasDocumentation`` (via entity ``_documentation_urls``) —
  a flat de-duped list of genuine project documentation URLs confirmed (or
  newly found) by the LLM.

- ``gme-internal:testCoverage`` (via entity ``_test_coverage``) — only used
  as a **fallback** when the Phase-1 deterministic regex found nothing.

Mirrors the ``ror_parent`` agent pattern: small typed input/output schemas,
``_SYSTEM_PROMPT = load_prompt(...)``, and a runner that returns ``None``
on failure (best-effort, never raises).
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, ConfigDict, Field

from git_metadata_extractor.agents.llm._loader import load_prompt
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "git_metadata_extractor.agents.llm.refiners.repo_signals.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")

_LLM_CALL_TIMEOUT_SECONDS = 60.0


class RepoSignalsInput(BaseModel):
    """Context for one README → documentation/coverage extraction call."""

    model_config = ConfigDict(populate_by_name=True)

    repository_handle: str
    readme_excerpt: str
    candidate_documentation_urls: list[str] = Field(default_factory=list)


class RepoSignalsPatch(BaseModel):
    """LLM output: confirmed/found documentation URLs + optional coverage."""

    model_config = ConfigDict(extra="forbid")

    documentation_urls: list[str] = Field(
        default_factory=list,
        description=(
            "URLs that are project DOCUMENTATION (readthedocs, a docs site, "
            "GitHub Pages docs, a wiki, an official manual) — NOT the repo "
            "itself, badges, CI, or unrelated links."
        ),
    )
    test_coverage: str | None = Field(
        default=None,
        description=(
            "Test coverage as a percentage string like '87%' if the README "
            "states a numeric coverage, else null."
        ),
    )


async def run_repo_signals(
    inp: RepoSignalsInput,
    *,
    llm_runtime: V2LLMRuntime | None = None,
    llm_call_timeout_seconds: float = _LLM_CALL_TIMEOUT_SECONDS,
) -> RepoSignalsPatch | None:
    """Run the repo-signals LLM refiner. Returns ``None`` on any failure.

    Best-effort: any exception leaves the caller's entity unchanged.
    """
    runtime = llm_runtime or V2LLMRuntime()
    identifier = inp.repository_handle

    import json  # noqa: PLC0415 — keep top-level imports lean for fast cold path

    user_prompt = (
        "Inspect the README excerpt and candidate URLs below. "
        "Return the genuine documentation URLs for this project and "
        "the numeric test-coverage percentage (if explicitly stated).\n\n"
        "```json\n"
        + json.dumps(inp.model_dump(by_alias=True), ensure_ascii=True, sort_keys=True)
        + "\n```"
    )

    logger.info("%s — calling repo_signals LLM", identifier)
    try:
        llm_result = await asyncio.wait_for(
            runtime.run_json_prompt(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                output_type=RepoSignalsPatch,
                tools=[],
            ),
            timeout=llm_call_timeout_seconds,
        )
    except TimeoutError:
        logger.warning(
            "%s — repo_signals LLM call timed out after %.1fs",
            identifier,
            llm_call_timeout_seconds,
        )
        return None
    except LLMRuntimeError:
        logger.warning("%s — repo_signals LLM runtime error", identifier, exc_info=True)
        return None
    except Exception:  # noqa: BLE001
        logger.warning("%s — repo_signals unexpected error", identifier, exc_info=True)
        return None

    if isinstance(llm_result.payload, RepoSignalsPatch):
        patch = llm_result.payload
    else:
        try:
            patch = RepoSignalsPatch.model_validate(llm_result.payload)
        except Exception:  # noqa: BLE001
            logger.warning(
                "%s — repo_signals returned an unparseable payload; skipping",
                identifier,
            )
            return None

    logger.info(
        "%s — repo_signals: doc_urls=%d coverage=%r",
        identifier,
        len(patch.documentation_urls),
        patch.test_coverage,
    )
    return patch


__all__ = [
    "RepoSignalsInput",
    "RepoSignalsPatch",
    "run_repo_signals",
]
