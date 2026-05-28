"""Factory for the terminal-agent + subagent variant runtime.

Constructs a `TerminalRunner` with two overrides relative to the
default monolith runtime:

1. ``extra_extensions`` adds pi-mono's subagent tool extension so the
   orchestrator can call ``subagent`` (single / parallel / chain modes).
2. ``mission_template_path`` points at this module's
   ``prompts/orchestrator.md`` template, which instructs the LLM to
   delegate per-entity work to subagents instead of building the graph
   itself.

Project-local agent definitions are auto-discovered by pi from the
nearest ``.pi/agents/`` directory above the run's cwd — for our setup
that resolves to ``<repo_root>/.pi/agents/{repo,person,org,article,
membership,contribution}.md``.
"""

from __future__ import annotations

from pathlib import Path

from src.v2.agents.terminal.runner import TerminalRunCaps, TerminalRunner

_HERE = Path(__file__).parent
_SUBAGENT_EXTENSION = _HERE / "pi_extension" / "subagent" / "index.ts"
_ORCHESTRATOR_MISSION = _HERE / "prompts" / "orchestrator.md"


def build_subagent_runner(
    *,
    output_dir: Path,
    executor_model: str,
    executor_base_url: str,
    executor_api_key_env: str,
    skills: list[str],
    caps: TerminalRunCaps,
    harness_kind: str = "pi",
    harness_binary: str = "pi",
    harness_extra_args: list[str] | None = None,
) -> TerminalRunner:
    """Construct a TerminalRunner wired for orchestrator + subagents."""
    return TerminalRunner(
        output_dir=output_dir,
        executor_model=executor_model,
        executor_base_url=executor_base_url,
        executor_api_key_env=executor_api_key_env,
        skills=skills,
        caps=caps,
        harness_kind=harness_kind,
        harness_binary=harness_binary,
        harness_extra_args=list(harness_extra_args or []),
        extra_extensions=[_SUBAGENT_EXTENSION],
        mission_template_path=_ORCHESTRATOR_MISSION,
        # Pi's `--tools` allowlist applies to extension tools too — the
        # subagent extension registers a tool literally named "subagent"
        # which we must explicitly allow, or the LLM will see "Tool
        # subagent not found" and (as observed in the first smoke test)
        # silently improvise mock outputs to satisfy the MISSION.
        extra_allowed_tools=["subagent"],
    )


__all__ = ["build_subagent_runner"]
