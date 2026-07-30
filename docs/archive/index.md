# Archive

Working notes kept for provenance, **not** as documentation. They describe
decisions, investigations and one-off fixes from earlier iterations — most
predate the `3.0.0` repo split and the `src/v2` → `git_metadata_extractor`
rename, so paths, module names and endpoints in them are frequently wrong.

**Do not follow instructions from these pages.** For anything current, start at
[Home](../index.md), [Getting Started](../getting-started.md), the
[V2 Extract Pipeline](../v2-pipeline.md), or the
[Architecture Overview](../architecture/overview.md).

They were moved out of the main navigation on 2026-07-29 (`3.0.0`), when about
half the published site turned out to be historical notes. Nothing was deleted.

## Design and strategy notes

| Note | Subject |
|---|---|
| [V1 Repository Analysis Strategy](AGENT_STRATEGY.md) | how the original v1 agent approached a repository |
| [Academic Catalog Option B](ACADEMIC_CATALOG_OPTION_B_IMPLEMENTATION.md) | the catalog design that was chosen |
| [Academic Catalog Refactor](ACADEMIC_CATALOG_REFACTOR_SUMMARY.md) | what that refactor changed |
| [Affiliation Changes](AFFILIATION_CHANGES.md) | earlier affiliation modelling; superseded by the resolver stages |

## Infoscience investigations

| Note | Subject |
|---|---|
| [Infoscience Integration](INFOSCIENCE_INTEGRATION.md) | how the integration was built |
| [Infoscience API Findings](INFOSCIENCE_API_FINDINGS.md) | endpoint behaviour observed while building it |

## JSON-LD conversion history

Four overlapping notes from the period when the JSON → JSON-LD mapping was
being worked out. The current behaviour lives in
`git_metadata_extractor/schema/` and the `build_jsonld_output` stage.

| Note | Subject |
|---|---|
| [JSON-LD Conversion Guide](JSONLD_CONVERSION.md) | the conversion approach |
| [Pydantic ↔ JSON-LD Mapping](PYDANTIC_JSONLD_MAPPING.md) | model-to-graph field mapping |
| [JSON-LD Conversion Summary](JSONLD_CONVERSION_SUMMARY.md) | summary of the above |
| [JSON-LD Mapping Update](JSONLD_MAPPING_UPDATE.md) | a later revision |
| [JSON / JSON-LD CLI](JSON_JSONLD_CONVERSION_CLI.md) | a conversion CLI from that era |

## Point-in-time fixes

| Note | Subject |
|---|---|
| [Estimated Tokens Fix](ESTIMATED_TOKENS_FIX.md) | token-estimation bug write-up |
| [Updates Summary](UPDATES_SUMMARY.md) | a batch of changes, since released |
| [GIMIE / GitHub CFF Edge Cases](issue-brief-gimie-github-cff-edge-cases.md) | `CITATION.cff` parsing edge cases — still useful background on GIMIE quirks |

## Releases

[Legacy Releases](../releases/legacy-releases.md) — release notes predating
`3.0.0`.
