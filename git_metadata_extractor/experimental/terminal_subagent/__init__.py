"""V2 terminal-agent subagent variant.

Sister module to ``git_metadata_extractor/experimental/terminal/`` that adds a fan-out
architecture on top of the same runtime. Where the plain terminal
runner asks one pi process to build the entire JSON-LD graph, this
variant runs an *orchestrator* pi process that delegates each entity
type to a specialised subagent (one per ``schema:Person``, one per
``org:Organization``, etc.) defined as a project-local markdown file
under ``.pi/agents/``.

The runner is a thin factory: it constructs a `TerminalRunner` with
the orchestrator MISSION template and pi-mono's subagent extension
loaded alongside the bash-blacklist extension. Everything else
(workdir layout, gimie + clone, scrubber, SHACL, judge) is reused.

This module exists side-by-side with `terminal/` so the simpler
single-agent recipe stays unchanged. Pick whichever the workload
needs: monolith for small repos, subagents when fan-out parallelism
or per-entity context isolation matters.
"""

from git_metadata_extractor.experimental.terminal_subagent.runner import build_subagent_runner

__all__ = ["build_subagent_runner"]
