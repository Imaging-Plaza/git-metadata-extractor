# Conversion Scripts

This directory contains utility scripts for working with the git-metadata-extractor.

## Available Scripts

### convert_json_jsonld.py

Command-line tool for converting between JSON and JSON-LD formats.

**Quick Start:**

```bash
# Convert JSON to JSON-LD
python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld

# Convert JSON-LD to JSON
python scripts/convert_json_jsonld.py to-json input.jsonld output.json
```

**Documentation:** See [JSON-LD Conversion CLI Guide](../docs/JSON_JSONLD_CONVERSION_CLI.md)

## Usage Examples

### Example 1: Convert Repository Metadata

```bash
python scripts/convert_json_jsonld.py to-jsonld \
    src/files/output_file.json \
    src/files/output_file.jsonld \
    --base-url https://github.com/your-org/your-repo
```

### Example 2: Round-trip Conversion

```bash
# Original → JSON-LD
python scripts/convert_json_jsonld.py to-jsonld data.json data.jsonld

# JSON-LD → Back to JSON
python scripts/convert_json_jsonld.py to-json data.jsonld data_restored.json
```

### Example 3: Batch Processing

```bash
# Convert all JSON files in a directory
for json_file in data/*.json; do
    base_name=$(basename "$json_file" .json)
    python scripts/convert_json_jsonld.py to-jsonld \
        "$json_file" \
        "output/${base_name}.jsonld"
done
```

## Requirements

Make sure you have the project dependencies installed:

```bash
pip install -e .
# Or with uv
uv sync
```

## See Also

- [Pydantic↔JSON-LD Mapping Documentation](../docs/PYDANTIC_JSONLD_MAPPING.md)
- [Quick Reference Guide](../docs/JSONLD_CONVERSION_SUMMARY.md)
- [Detailed CLI Guide](../docs/JSON_JSONLD_CONVERSION_CLI.md)
