# Sources service boundary implementation plan

**Goal:** Make the extractor a client of the sources HTTP service, with no
installed sources-library dependency, index storage access, query embedding,
or index bootstrap. Keep extraction, ontology mapping, validation and caches
in GME. Preserve the independent sources checkout at `dev/open-pulse-sources`.

**Architecture:** Move the existing retrieval implementation to sources and
expose authenticated, bounded operations. Retain thin HTTP adapters with
compatible interfaces in GME so entity tools and deterministic stages keep
their behavior. Do not replace unavailable services with hidden local reads.

**Execution:** Use the subagent-driven-development skill for implementation
groups and independent review. GME remains on `feat/2026-11-refactor`; the
sources checkout is on `feat/extractor-service-boundary`. Do not merge, publish
images, modify secrets, or touch live index data. Source work remains local
until explicitly authorized for publication.

- [x] Locate and safely relocate the independent sources Git repository;
  verify clean state/history and exclusion from GME tracking/build context.
- [x] Audit local retrieval, pipeline, write-through, packaging and deployment
  dependencies. Preserve filters, scopes, rerank policy, hydration, lineage,
  federated lookup, exact affiliations, ancestor expansion and opt-in writes.
- [x] Move retrieval providers to sources; add typed HTTP operations and
  client adapters, with transport and service tests. Preserve existing public
  sources endpoints and bound every new operation.
- [x] Add source-owned lookup/annotation/ensure-indexed operations and migrate
  GME post-processing/auto-ingest to them, keeping inference in GME.
- [x] Use metadata from the actual remote service for source snapshots; own
  pure DOI normalization locally; remove sources/storage package dependencies
  and GME bootstrap/config copies. Update local Compose and setup/docs.
- [x] Verify both suites, strict docs, import isolation without sources,
  and cross-service calls with isolated fake/temporary stores. Review changes
  and document deployment compatibility and remaining limitations.

## Verification focus

- No sources server: explicit unavailable result/warning, never local fallback.
- Transport contract: preserve selected scopes, filters, reranking and IDs;
  reject malformed/oversized requests and authenticate every added route.
- Resource lifecycle: one owned HTTP pool per runtime; close on failure too.
- Provenance: unavailable source version creates no invented snapshot;
  concurrent runs/services do not share metadata accidentally.
- Writes: extraction auto-ingest stays opt-in and source-owned; report job
  acceptance separately from completion; skip existing records as before.
- No live ingest, embedding, resets or model calls during verification.

## Location ruling

The user-provided `D:/pro/open-pulse-project/open-pulse-sources` was absent.
The matching clean checkout was at
`D:/pro/git-metadata-extractor-project/open-pulse-sources`, with the expected
`sdsc-ordes/open-pulse-sources` remote and HEAD `9beef914`. That entire directory
was moved, rather than cloned or reconstructed, preserving ignored local data.

## Completed validation (2026-11-12)

Sources commit: `3b66de74d03ced573e0b4e3a4f1a6ac12398893e` on
`feat/extractor-service-boundary` (local, unpublished). GME remains on
`feat/2026-11-refactor`. Both repositories preserve their independent history.

- GME: **1,894 passed, 1 skipped** with sources, DuckDB, Qdrant, FAISS,
  graphai, PyAlex and Hugging Face distributions uninstalled in a disposable
  container. Live-provider and LLM-integration tests excluded.
- Sources: **811 passed**, network disabled, temporary index/cache paths,
  cached tokenizer asset, and DuckDB **1.5.4** from the sources lockfile.
  The original unconstrained validation image installed DuckDB 2.0 and showed
  existing failures on both branches; the production Dockerfile now installs
  the frozen lock rather than silently upgrading dependencies.
- Cross-repository HTTP/ASGI contracts: **2 passed**. Final import/deployment
  boundary checks: **5 passed**. Migrated HTML fixture checks after whitespace
  normalization: **20 passed**.
- Both application images built. A temporary internal Docker network verified
  actual HTTP authentication, source-owned temporary DuckDB lookup, remote
  service-version scoping, and fixture-backed rule-based extraction with a run
  report. Production images supplied the code; test fixtures were mounted only
  for mock extraction. No existing volumes, services or live records were used.
- Both Compose files validate. Strict Pages build and browser checks passed;
  the revised C4 container diagram was visually inspected.
- Independent reviews covered retrieval scopes, lineage without embedding
  credentials, auxiliary-operation error propagation, writer coordination and
  provider lifecycle. Reported defects were fixed and regression-tested.

No live upstream-provider, ingestion, embedding or LLM calls were made. Live
semantic retrieval remains dependent on the operator's existing source index
and embedding configuration. Published sources 0.1.2 lacks the new routes:
use the paired local sources branch until a compatible release is published.
Existing index data and secret files were preserved; no remote pushes or image
publication were performed.
