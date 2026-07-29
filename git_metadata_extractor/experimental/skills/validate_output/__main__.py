"""CLI entry point for the `validate_output` skill.

Lets the executor agent self-check its `output.jsonld` against the v2
SHACL shapes graph BEFORE declaring done. Returns the same conforms /
violations / warnings shape that the runner's `_validate` produces, so
the agent can iterate on its own output until SHACL conforms.

This skill is the agent's structural-correctness loop. The judge keeps
its focus on coverage + groundedness; structural issues never need to
reach it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from git_metadata_extractor.experimental.skills._runtime import SkillError, emit_error, emit_success

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gme-validate-output",
        description=(
            "Validate output.jsonld against the v2 SHACL shapes graph. "
            "Returns {conforms, violations, warnings}."
        ),
    )
    parser.add_argument(
        "--path",
        default="output.jsonld",
        help="Path to the JSON-LD file to validate (default: ./output.jsonld).",
    )
    parser.add_argument(
        "--max-violations",
        type=int,
        default=50,
        help="Truncate the violations list at this many entries (default: 50).",
    )
    return parser


def _strip_internal_metadata(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            k: _strip_internal_metadata(v)
            for k, v in payload.items()
            if not (isinstance(k, str) and k.startswith("_"))
        }
    if isinstance(payload, list):
        return [_strip_internal_metadata(v) for v in payload]
    return payload


def _run(args: argparse.Namespace) -> dict[str, Any]:
    target = Path(args.path)
    if not target.exists():
        raise SkillError(f"file not found: {target}", kind="not_found")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise SkillError(f"jsonld parse failed: {err}", kind="invalid_json") from err

    try:
        from rdflib import Graph as RDFGraph  # noqa: PLC0415

        from git_metadata_extractor.validation import (  # noqa: PLC0415
            SHACLRuntimeUnavailableError,
            SHACLValidator,
            load_ontology_shapes_graph,
        )
    except ImportError as err:
        raise SkillError(f"shacl deps missing: {err}", kind="provider_unavailable") from err

    try:
        data_graph = RDFGraph()
        data_graph.parse(data=json.dumps(_strip_internal_metadata(payload)), format="json-ld")
    except Exception as err:  # noqa: BLE001
        raise SkillError(f"jsonld -> rdf parse failed: {err}", kind="invalid_jsonld") from err

    try:
        result = SHACLValidator().validate_graph(data_graph, load_ontology_shapes_graph())
    except SHACLRuntimeUnavailableError as err:
        raise SkillError(str(err), kind="provider_unavailable") from err
    except Exception as err:  # noqa: BLE001
        raise SkillError(f"shacl validate failed: {err}", kind="shacl_error") from err

    violations = [_violation(v) for v in result.violations[: args.max_violations]]
    warnings = [_violation(w) for w in result.warnings[: args.max_violations]]
    return {
        "conforms": result.conforms,
        "violation_count": len(result.violations),
        "warning_count": len(result.warnings),
        "violations": violations,
        "warnings": warnings,
        "path": str(target),
    }


def _violation(v: dict[str, Any]) -> dict[str, Any]:
    return {
        "focus": v.get("focusNode"),
        "path": v.get("path") or v.get("resultPath"),
        "value": v.get("value"),
        "severity": v.get("severity"),
        "message": v.get("message"),
    }


def main(argv: list[str] | None = None) -> int:
    level_name = os.environ.get("V2_SKILL_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result = _run(args)
    except SkillError as err:
        emit_error(err)
        return 1
    except Exception as err:  # noqa: BLE001 — final boundary
        logger.exception("validate_output failed")
        emit_error(err)
        return 2
    emit_success(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
