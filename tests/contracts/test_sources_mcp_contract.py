"""Optional MCP parity checks; no sources package is required by GME at runtime."""

# ruff: noqa: PLC0415 - optional sources imports stay inside paired tests
import ast
from pathlib import Path

import pytest

pytest.importorskip(
    "open_pulse_sources.mcp_server.catalog",
    reason="MCP parity requires the independent sources checkout",
)


def test_existing_source_tool_names_parameters_and_defaults_are_preserved():
    from open_pulse_sources.mcp_server.catalog import retrieval_tools
    from open_pulse_sources.mcp_server.direct import direct_tools
    from open_pulse_sources.mcp_server.discovery import discovery_tools

    specs = {spec.name: spec for spec in [*retrieval_tools(), *direct_tools(), *discovery_tools()]}
    tools_dir = Path(__file__).resolve().parents[2] / "git_metadata_extractor/agents/llm/agent_tools"
    matched = set()
    for path in tools_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = {
            target.id: node.value
            for node in tree.body if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) or node.name not in specs:
                continue
            matched.add(node.name)
            fields = specs[node.name].input_model.model_fields
            arguments = node.args.args
            for argument in arguments:
                assert argument.arg in fields, (node.name, argument.arg)
            defaults = zip(arguments[-len(node.args.defaults):], node.args.defaults, strict=True) if node.args.defaults else []
            for argument, default in defaults:
                value = constants[default.id] if isinstance(default, ast.Name) else default
                assert fields[argument.arg].default == ast.literal_eval(value), (node.name, argument.arg)
    assert matched == set(specs), set(specs) - matched
