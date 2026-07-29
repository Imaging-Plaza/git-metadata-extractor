# Updates Summary

This page summarizes the current documentation baseline for `v2.0.0`.

## Documentation structure now aligned with code

- API and runtime entrypoints documented from `git_metadata_extractor/app.py` and `src/analysis/*`.
- Architecture diagrams updated to reflect repository, user, and organization flows.
- Infoscience and academic catalog pages updated to match current tool and model names.
- JSON-LD docs now reference the active conversion implementation in `src/data_models/conversion.py`.

## Key behavior notes now explicitly documented

- Cache-first execution with `force_refresh` bypass behavior.
- Optional enrichment toggles (`enrich_orgs`, `enrich_users`).
- API `stats` payload includes token and timing metadata.
- CLI status clarified: legacy `src/main.py` imports need refresh in current layout.

## Remaining known implementation gaps called out in docs

- Repository author-level linked-entity assignment remains a scaffolded follow-up path.
- Reverse JSON-LD conversion is strongest for repository graphs; user/org reverse paths remain simplified.
