# ruff: noqa: INP001
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[2]
STRICT_SCHEMA_DIR = REPO_ROOT / "git_metadata_extractor" / "schema" / "json" / "strict"
AGENT_SCHEMA_DIR = REPO_ROOT / "git_metadata_extractor" / "schema" / "json" / "agent"
GENERATED_MODELS_PATH = REPO_ROOT / "git_metadata_extractor" / "schema" / "models" / "strict.py"
GENERATED_AGENT_MODELS_PATH = REPO_ROOT / "git_metadata_extractor" / "schema" / "models" / "agent.py"

MODEL_SCHEMA_FILES: Final[dict[str, str]] = {
    "PersonModel": "person.schema.json",
    "RepositoryModel": "repository.schema.json",
    "OrganizationModel": "organization.schema.json",
    "MembershipModel": "membership.schema.json",
    "ContributionModel": "contribution.schema.json",
    "ArticleModel": "article.schema.json",
}

AGENT_MODEL_SCHEMA_FILES: Final[dict[str, str]] = {
    "AgentPersonShape": "person.schema.json",
    "AgentRepositoryShape": "repository.schema.json",
    "AgentOrganizationShape": "organization.schema.json",
    "AgentMembershipShape": "membership.schema.json",
    "AgentContributionShape": "contribution.schema.json",
    "AgentArticleShape": "article.schema.json",
}

HASH_MARKER: Final[str] = "# source-schema-sha256:"


def _schema_hashes(schema_dir: Path, schema_files: dict[str, str]) -> dict[str, str]:
    return {
        schema_name: hashlib.sha256(
            (schema_dir / schema_name).read_bytes(),
        ).hexdigest()
        for schema_name in sorted(schema_files.values())
    }


def _build_embedded_bundle(
    path: Path,
    schema_dir: Path,
    model_schema_files: dict[str, str],
    bundle_title: str,
) -> None:
    bundle: dict[str, object] = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": bundle_title,
        "type": "object",
        "properties": {},
        "$defs": {},
    }
    properties = bundle["properties"]
    definitions = bundle["$defs"]

    assert isinstance(properties, dict)
    assert isinstance(definitions, dict)

    for model_name, schema_name in model_schema_files.items():
        schema_payload = json.loads((schema_dir / schema_name).read_text())
        schema_payload["title"] = model_name
        definitions[model_name] = schema_payload
        property_name = model_name.lower()
        properties[property_name] = {"$ref": f"#/$defs/{model_name}"}

    path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")


def _datamodel_codegen_command(
    bundle_path: Path,
    output_path: Path,
    *,
    extra_fields: str = "forbid",
) -> list[str]:
    local_codegen = REPO_ROOT / ".venv" / "bin" / "datamodel-codegen"
    executable = local_codegen if local_codegen.exists() else Path("datamodel-codegen")
    return [
        str(executable),
        "--input",
        str(bundle_path),
        "--input-file-type",
        "jsonschema",
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.10",
        "--use-title-as-name",
        "--field-constraints",
        "--disable-timestamp",
        "--extra-fields",
        extra_fields,
        "--formatters",
        "ruff-format",
        "ruff-check",
        "--output",
        str(output_path),
    ]


def _run_codegen(
    bundle_path: Path,
    output_path: Path,
    *,
    extra_fields: str = "forbid",
) -> None:
    command = _datamodel_codegen_command(bundle_path, output_path, extra_fields=extra_fields)
    try:
        subprocess.run(command, cwd=REPO_ROOT, check=True)  # noqa: S603
    except FileNotFoundError as exc:
        message = (
            "datamodel-code-generator is not installed. "
            "Run `uv pip install --python .venv/bin/python datamodel-code-generator`."
        )
        raise RuntimeError(message) from exc
    except subprocess.CalledProcessError as exc:
        joined = " ".join(command)
        message = f"datamodel-code-generator failed: {joined}"
        raise RuntimeError(message) from exc


def _render_hash_block(schema_hashes: dict[str, str]) -> list[str]:
    lines = [HASH_MARKER]
    lines.extend(
        f"# - {schema_name}: {schema_hashes[schema_name]}"
        for schema_name in sorted(schema_hashes)
    )
    lines.append("#")
    return lines


def _inject_hash_block(content: str, schema_hashes: dict[str, str]) -> str:
    lines = content.splitlines()
    insert_index = 0
    while insert_index < len(lines) and lines[insert_index].startswith("#"):
        insert_index += 1

    block = _render_hash_block(schema_hashes)
    with_hashes = lines[:insert_index] + block + lines[insert_index:]
    return "\n".join(with_hashes).rstrip() + "\n"


def _extract_embedded_hashes(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        marker_index = lines.index(HASH_MARKER)
    except ValueError:
        return {}

    parsed_hashes: dict[str, str] = {}
    for line in lines[marker_index + 1 :]:
        if line == "#":
            break
        prefix = "# - "
        if not line.startswith(prefix):
            break
        payload = line[len(prefix) :]
        if ": " not in payload:
            continue
        name, digest = payload.split(": ", 1)
        parsed_hashes[name] = digest
    return parsed_hashes


def _render_expected_output(
    schema_dir: Path,
    model_schema_files: dict[str, str],
    bundle_title: str,
    output_filename: str,
    *,
    extra_fields: str = "forbid",
) -> str:
    hashes = _schema_hashes(schema_dir, model_schema_files)
    with tempfile.TemporaryDirectory(prefix="v2-model-codegen-") as tmp_dir:
        temp_dir = Path(tmp_dir)
        bundle_path = temp_dir / "bundle.schema.json"
        raw_output_path = temp_dir / output_filename
        _build_embedded_bundle(bundle_path, schema_dir, model_schema_files, bundle_title)
        _run_codegen(bundle_path, raw_output_path, extra_fields=extra_fields)
        generated_content = raw_output_path.read_text(encoding="utf-8")
    return _inject_hash_block(generated_content, hashes)


def _check_one_models_file(
    output_path: Path,
    schema_dir: Path,
    model_schema_files: dict[str, str],
    bundle_title: str,
    *,
    extra_fields: str = "forbid",
) -> int:
    if not output_path.exists():
        print(f"Missing generated models file: {output_path}")
        print("Run: just v2-models-generate")
        return 1

    expected = _render_expected_output(
        schema_dir,
        model_schema_files,
        bundle_title,
        output_path.name,
        extra_fields=extra_fields,
    )
    actual = output_path.read_text(encoding="utf-8")
    if actual == expected:
        print(f"{output_path.name}: up-to-date.")
        return 0

    current_hashes = _schema_hashes(schema_dir, model_schema_files)
    embedded_hashes = _extract_embedded_hashes(output_path)
    changed_schemas = [
        schema_name
        for schema_name in sorted(current_hashes)
        if embedded_hashes.get(schema_name) != current_hashes[schema_name]
    ]
    if changed_schemas:
        print(
            f"{output_path.name}: schema drift in "
            + ", ".join(changed_schemas)
            + ". Regenerate models.",
        )
    else:
        print(f"{output_path.name}: stale (codegen output drifted). Regenerate models.")

    print("Run: just v2-models-generate")
    return 1


def _generate_one_models_file(
    output_path: Path,
    schema_dir: Path,
    model_schema_files: dict[str, str],
    bundle_title: str,
    *,
    extra_fields: str = "forbid",
) -> int:
    expected = _render_expected_output(
        schema_dir,
        model_schema_files,
        bundle_title,
        output_path.name,
        extra_fields=extra_fields,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(expected, encoding="utf-8")
    print(f"Generated {output_path.relative_to(REPO_ROOT)}")
    return 0


def _check_models_file() -> int:
    rc = _check_one_models_file(
        GENERATED_MODELS_PATH,
        STRICT_SCHEMA_DIR,
        MODEL_SCHEMA_FILES,
        "EntitiesBundle",
        extra_fields="forbid",
    )
    rc |= _check_one_models_file(
        GENERATED_AGENT_MODELS_PATH,
        AGENT_SCHEMA_DIR,
        AGENT_MODEL_SCHEMA_FILES,
        "AgentEntitiesBundle",
        extra_fields="ignore",
    )
    return rc


def _generate_models_file() -> int:
    rc = _generate_one_models_file(
        GENERATED_MODELS_PATH,
        STRICT_SCHEMA_DIR,
        MODEL_SCHEMA_FILES,
        "EntitiesBundle",
        extra_fields="forbid",
    )
    rc |= _generate_one_models_file(
        GENERATED_AGENT_MODELS_PATH,
        AGENT_SCHEMA_DIR,
        AGENT_MODEL_SCHEMA_FILES,
        "AgentEntitiesBundle",
        extra_fields="ignore",
    )
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify v2 models.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify generated models are in sync with schemas.",
    )
    args = parser.parse_args()
    if args.check:
        return _check_models_file()
    return _generate_models_file()


if __name__ == "__main__":
    raise SystemExit(main())
