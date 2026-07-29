"""Experimental subsystems — not part of the production extraction pipeline.

terminal/ + terminal_subagent/ + skills/: the pi-based terminal-agent PoC
(an executor LLM driving CLI skills in a sandboxed checkout). Kept apart
so the production agents/ package stays purely the rule_based/llm/refiner
runtimes.
"""
