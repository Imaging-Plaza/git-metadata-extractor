# JSON <-> JSON-LD Conversion CLI

Use the conversion script in this repository:

- `scripts/convert_json_jsonld.py`

## Commands

### Convert JSON to JSON-LD

```bash
python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld
```

Optional base URL for `@id` generation:

```bash
python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld \
  --base-url https://github.com/org/repo
```

### Convert JSON-LD to JSON

```bash
python scripts/convert_json_jsonld.py to-json input.jsonld output.json
```

## What the script validates

- Detects model type from API wrapper (`type` + `output`) or model-shaped JSON.
- Validates with `SoftwareSourceCode`, `GitHubUser`, or `GitHubOrganization` models.
- Uses conversion helpers from `src/data_models/conversion.py`.

## Current limitations

- Reverse conversion is strongest for repository graphs.
- User/organization reverse conversion paths are currently simplified and marked with TODO comments in the script.

## Typical workflow

1. Call API endpoint (`/v1/repository/llm/json` or `/v1/repository/llm/json-ld`).
2. Save response payload.
3. Convert using this script as needed for downstream tooling.
