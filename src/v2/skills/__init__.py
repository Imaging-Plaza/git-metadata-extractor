"""V2 terminal-agent skills.

Each subpackage here is a pi-loadable skill: a CLI tool with a README that
the executor LLM consumes. Skills are intentionally subprocess-portable —
any harness that can spawn a command can use them, not just pi. See
`config/v2/terminal_agent.yml` for the active skill list.
"""
