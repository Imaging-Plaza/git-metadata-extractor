# JSON-LD Mapping Update Notes

This page tracks the current mapping baseline in `v2.0.0`.

## Namespace baseline

The active JSON-LD context includes:

- `schema`: Schema.org
- `sd`: OKN software description vocabulary
- `pulse`: Open Pulse ontology
- `md4i`: metadata4ing

## Current mapping source of truth

- `src/data_models/conversion.py`
  - `PYDANTIC_TO_ZOD_MAPPING`
  - `JSONLD_TO_PYDANTIC_MAPPING`
  - `convert_pydantic_to_jsonld`
  - `convert_jsonld_to_pydantic`

## Practical implications

- Repository, user, and organization model fields can be serialized to JSON-LD via shared mappings.
- Linked academic entities and EPFL assessment properties are represented with `pulse:*` fields.
- Conversion behavior should be treated as code-driven; update docs after mapping changes in `conversion.py`.
