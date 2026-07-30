# Task 04 — Cross-repository compatibility contract

**Severity:** P1 · **Status: core items DONE 2026-07-15** (import-contract
test + HF provider fix in the parent; 503 error mapping in the child;
remaining: store-compat fixtures, cross-repo CI matrix, full route-contract
suite) · **Repositories:** both

## Objective

Prove that the parent read adapters and auto-ingest callers remain compatible
with the exact child library/service/store revision they deploy with.

## Confirmed problem

The parent imports many child implementation modules directly, including
private namespaces such as `_shared`, `_rcp`, and `_federated`. Local child
tests cannot detect imports originating in the parent.

A concrete stale import exists:

- Parent `src/v2/ingest/providers/huggingface_rag.py:315` imports
  `open_pulse_sources.index.huggingface.config`.
- The child ships an `index.huggingface` package (embed/ingest/rerank/
  retrieval/storage/vector), but it has **no `config.py`** — the split
  per-entity packages (`huggingface_models`, `huggingface_datasets`, …)
  own config now.
- The parent catches the resulting error and returns `None`, silently disabling
  the HuggingFace RAG provider.

**Corrected provenance (verified 2026-07-14):** this is **not a split
regression**. `src/index/huggingface/config.py` was deleted by monolith
commit `9a5aa2b` ("retire legacy huggingface catch-all module (H7+H8)"),
*before* the split base `64f1d14` — the HuggingFace RAG provider has been
silently disabled in every deployment since that commit. This raises the
urgency (check production logs for the
`failed to load config` warning) and defines the fix: point
`build_default_provider` at the split config sources
(`_huggingface_base.config_base` / `huggingface_models.config`), and add
the regression test below. It is also the canonical example of why this
task exists: a swallowed `ImportError` hid a dead feature for weeks.

Fourteen parent `*_rag.py` providers, pipeline stages, DOI canonicalization,
SNSF facet reads, and auto-ingest hooks depend on child APIs. Existing tests
cover only part of this surface.

## Implementation steps

1. Build/install the child wheel in an isolated environment with the parent.
2. Inventory every `open_pulse_sources` import in parent `src/` and tests.
3. Add an import-contract test that imports each symbol and reports the exact
   missing path; do not swallow errors.
4. Fix the stale HuggingFace config import using the correct child config
   source and add a regression test proving provider construction is non-`None`
   under valid mock config.
5. Instantiate every parent RAG provider with fake Qdrant/RCP dependencies.
6. Validate collection names, filter payloads, hydration record shapes, and
   canonical IDs against child definitions.
7. Exercise parent DOI re-export and other shared utilities.
8. Exercise all four auto-ingest call signatures against the child library, or
   replace them with service calls under Task 05.
9. Add API contract checks for moved routes:
   - paths and methods,
   - auth status codes,
   - request limits,
   - response/job schemas,
   - **error mapping for missing backing services** (2026-07-14 smoke run:
     `POST /v2/indices/zenodo_records/search` with no `RCP_TOKEN` returns a
     raw `500 Internal Server Error` from `config.require_rcp()` instead of
     a clean 503 with a `detail` body — the stats route already does this
     correctly; make search/ingest match).
10. Add a store compatibility smoke: child writes a temporary store/snapshot;
    parent reads/searches the matching data.
11. Run the contract job in both repositories or from a dedicated matrix that
    pins both revisions.

## Acceptance criteria

- Every parent child-library import resolves from the built wheel.
- No provider silently disables because of a stale module path.
- Parent adapters can consume stores and Qdrant payloads produced by the
  matching child revision.
- Management API compatibility is captured by automated tests.
- CI reports an actionable failure when either repository changes a shared
  contract.

## Recommended ownership

Keep adapter behavior tests in the parent and store/service contract fixtures in
the child. Share fixtures only through versioned package/API artifacts, not
filesystem-relative test imports.

