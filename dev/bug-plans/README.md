# GME bug-fix plans — triage index

Correction plans for the 13 issues in the 2026-06-12 bug report (GME Nostromo).
Each plan was prepared by investigating the **current** code; several **corrected
the original report** — see "Key finding" below. **No source code was changed** —
these are plans only.

| # | Issue | Plan | Severity | Effort | Confidence | Key finding vs. report |
|---|-------|------|----------|--------|-----------|------------------------|
| 1 | rule_based_disciplines DuckDB access-mode conflict (workers>1) | [01](01-disciplines-duckdb-access-mode.md) | medium | 0.5–1d | high | **Confirmed, cause re-attributed.** RAG search uses Qdrant, *not* DuckDB — the conflicting read-write handle comes from the **stats endpoint** + federated/semantic search, not the RAG path. Fix #1 (all inference opens read-only) holds. |
| 2 | Server destabilizes under concurrent hybrid jobs | [02](02-hybrid-concurrency-http-job-instability.md) | medium | 0.5–1d (short) / 2–4d (struct) | medium | Jobs run as `asyncio.create_task` on the **HTTP event loop**; blocking I/O (gimie fetch, github provider, `time.sleep`, OpenAlex `requests.get`) freezes the loop → resets. Wrap blocking calls in `to_thread`. |
| 3 | Per-extraction memory growth (0.5→5.1 GiB) | [03](03-extraction-memory-growth.md) | medium-high | ~1h stopgap / 3–5d full | medium | Hypothesis-driven (no profiler yet). Top suspects: per-run LLM clients never `aclose()`d, unbounded `_search_cache` (infoscience), unclosed provider `requests.Session`. Stopgap: gunicorn `--max-requests`. |
| 4 | Index-ingest starves extraction (no load isolation) | [04](04-ingest-extraction-load-isolation.md) | medium | 2–2.5d (B+D) / 4–6d (A) | high (mech) | Both ingest & extract are in-process `create_task` jobs sharing one thread pool + the SQLite cache writer-lock. Dedicated ingest pool now; separate ingest worker later. |
| 5 | dropped_affiliations parked as opaque hashes | [05](05-dropped-affiliations-structured.md) | low-medium | ~0.5–1d | high | Breadcrumb at `reconciliation.py:1739` lacks `text`/`source`/`unresolved`. Enrich the existing `gme-internal:dropped_affiliations` entry; no new ontology term. |
| 6 | V2_GITHUB_RAG_AUTO_INGEST "not firing" | [06](06-github-rag-auto-ingest.md) | low-medium | ~2h | high | **Real bug — env-var name mismatch.** Code reads `V2_GITHUB_REPOS_RAG_AUTO_INGEST`; only `V2_GITHUB_RAG_AUTO_INGEST` is documented (`.env.example:402`) and read by nothing. Fix the doc/canonicalize + add logs. |
| 7 | stub=true on fully-populated entities | [07](07-stub-flag-semantics.md) | low | ~0.5–1d | high (facts) | `_stub` is **write-only, never cleared** (3 writes, 0 reads), and can bleed onto full entities via merge helpers. Replace with `_extraction_level` + exclude `_*` keys in merges. |
| 8 | badges stored as opaque hash IDs | [08](08-badges-human-readable.md) | low | ~0.5d | high | Hash is an **incidental rdflib blank-node**, not a dedup ID. `label`/`image_url`/`link_url` exist but are unmapped in `@context` so they collapse. Emit flat scalar lists like `_releases`. |
| 9–11 | GitLab index: thin embed card · missing `require_rcp()` · `iter_public_users` admin-only | [09-11](09-11-gitlab-index-fixes.md) | medium | ~2–2.5h combined | high | All three confirmed. **The operator hot-patches for #9/#10 are NOT in this checkout** and must be (re-)landed. #10 (`require_rcp`) blocks all embeds → fix first. |
| 12 | snsf DuckDB bootstrap `Vector::Reference … different type` | [12](12-snsf-duckdb-bootstrap-type-error.md) | medium | ~0.5–1d | medium | Leading suspect: non-idempotent grant-id migration UPDATE (`duckdb_store.py:146`) force-casting arrays. Exact error **not reproduced** on DuckDB 1.5.3 — includes a bisection diagnosis. |
| 13 | github_organizations index "no DuckDB chunks table" | [13](13-github-organizations-index-consistency.md) | low | ~30m to close | high | **Premise no longer holds** — orgs already keep a `chunks` table via the shared base, identical to users. Likely stale report or `.ro.duckdb` snapshot misread. Recommend verify-and-close. |

## Reading order for fixing

1. **Quick, high-certainty wins:** #6 (env-var name), #9–11 (GitLab, re-land hot-patches), #13 (verify & close).
2. **Data-quality:** #5, #7, #8 (all internal/`gme-internal:` surface, additive, low risk).
3. **Concurrency & stability (the hard core):** #1 → #4 → #2 → #3. These interact — #1's read-only seam, #4's separate-ingest-worker, and #2's `to_thread` offload all touch the same DuckDB-writer / thread-pool boundary, so sequence and coordinate them.
4. **#12** snsf — confirm the offending statement on the real host first (the diagnosis plan), then apply.

## Notes

- Every plan states **status: investigated — plan ready (no code changed)**, cites `file:line`, and gives a verification plan.
- Confidence is the agent's own; #2, #3, #12 are explicitly partly hypothesis-driven and say what to confirm before committing a fix.
- Each plan can be handed to an implementer (or a follow-up agent) as a self-contained brief.
