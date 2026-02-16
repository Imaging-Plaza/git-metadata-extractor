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

### Example 4: Convert with Auto-detected Base URLs

```bash
# Regenerate JSON-LD files with proper GitHub URLs from source JSON
rm -rf data/1_batch_11122025/1_batch/converted/*

for file in data/1_batch_11122025/1_batch/entities-properties/*.json; do
  if [ -f "$file" ]; then
    base=$(basename "${file%.json}")
    link=$(.venv/bin/python -c "import json; print(json.load(open('$file')).get('link', ''))" 2>/dev/null)

    if [ -n "$link" ]; then
      .venv/bin/python scripts/convert_json_jsonld.py to-jsonld "$file" "data/1_batch_11122025/1_batch/converted/${base}.jsonld" --base-url "$link"
    else
      .venv/bin/python scripts/convert_json_jsonld.py to-jsonld "$file" "data/1_batch_11122025/1_batch/converted/${base}.jsonld"
    fi
    echo "Converted $base"
  fi
done
```

## Uploading to Tentris

### upload_all_to_tentris.sh

Batch upload script for uploading JSON-LD files to a Tentris triplestore.

**Setup:**

```bash
# Make the script executable
chmod +x scripts/upload_all_to_tentris.sh
```

**Usage:**

```bash
# Run the batch upload
./scripts/upload_all_to_tentris.sh
```

The script will:
1. Authenticate with Tentris
2. Convert each JSON-LD file to Turtle format
3. Upload to the Tentris graph store
4. Show progress and summary

**Clear the default graph before uploading:**

```bash
# Login to Tentris
curl -c "/tmp/tentris-cookie" \
    --data "username=YOUR_USERNAME&password=YOUR_PASSWORD" \
    http://YOUR_TENTRIS_HOST:PORT/login

# Clear the default graph
curl -b "/tmp/tentris-cookie" \
    -H "Content-Type: application/sparql-update" \
    --data "CLEAR DEFAULT" \
    http://YOUR_TENTRIS_HOST:PORT/update
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
