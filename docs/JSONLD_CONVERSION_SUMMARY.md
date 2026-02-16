# JSON-LD Conversion Summary

## What is stable now

- Repository JSON-LD output path is integrated in API.
- Conversion context uses `schema`, `sd`, `pulse`, and `md4i` namespaces.
- Token/runtime stats are returned alongside API output through `APIStats`.

## Quick conversion references

- Pydantic -> JSON-LD: `convert_pydantic_to_jsonld`
- JSON-LD -> Pydantic repository: `convert_jsonld_to_pydantic`
- CLI wrapper script: `scripts/convert_json_jsonld.py`

## Minimal expected JSON-LD shape

```json
{
  "@context": {"schema": "http://schema.org/"},
  "@graph": [
    {
      "@type": "schema:SoftwareSourceCode",
      "schema:name": "Example"
    }
  ]
}
```

## Known boundaries

- Reverse conversion currently targets repository graphs (`SoftwareSourceCode`) as primary supported model.
- User/organization reverse conversion in the script is marked simplified.
