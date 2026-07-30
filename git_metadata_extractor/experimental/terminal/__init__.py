"""V2 terminal-agent runtime PoC.

Third runtime alongside `llm` and `rule_based`. Spawns an external terminal
agent (pi.dev by default) per repo, hands it a per-run tempdir with the
gimie context and a clone, lets it call CLI skills from `git_metadata_extractor/experimental/skills/`,
and reads back a JSON-LD result that gets validated and judged by a
cross-vendor LLM.

This package is intentionally not wired into `/v2/extract` yet — the PoC
runs only via `scripts/v2/poc_terminal_agent.py`.
"""
