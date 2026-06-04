# GME operations runbook

Operational notes for running the v3.0.0 (`develop`) build of the
git-metadata-extractor against the Open Pulse index/RAG stores. Captures the
gotchas surfaced by real index/RAG enrichment runs.

## 1. Monolith → split data migration (required on existing deployments)

The 3.0.0 restructuring split the monolithic per-source DuckDBs
(`github.duckdb`, `huggingface.duckdb`, `zenodo.duckdb`) into one store per
entity type (`github_repos`, `huggingface_models`, `huggingface_datasets`,
`huggingface_organizations`, `huggingface_spaces`, `zenodo_records`). **The data
migration into the new layout is not automatic.** A deployment that only ran the
rc1 reindex populated the *old* paths/collections (`hf_models`, …), which the
split left orphaned — so a fresh `develop` deploy serves **empty HuggingFace /
GitHub / Zenodo split stores** until the migration runs. (This is finding #4.)

Run it with the GME server **stopped** (DuckDB is single-writer; the migration
opens the split stores read-write):

```bash
# dry run — reports per-table source/target row counts, no writes
python scripts/v2/migrate_monolith_to_split.py

# copy rows + rebuild Qdrant + republish the .ro.duckdb snapshot
python scripts/v2/migrate_monolith_to_split.py --apply --reembed
```

- The orphan monolithic files are opened **read-only** and never modified — they
  remain the canonical backup.
- `--reembed` drops each store's `chunks` table + Qdrant collection and rebuilds
  from scratch (use it after a bulk copy; plain `--embed` only embeds rows with
  no existing `chunks` bookkeeping and will no-op on freshly-copied rows).
- The migration now republishes the `<store>.ro.duckdb` snapshot the Hub reads
  from. **If you mutate a store by any other path, republish the snapshot** or
  the Hub keeps serving stale data:
  `python -c "from src.index._snapshot import publish_snapshot; ..."`.

### Entities with no monolith source

These never existed in the old layout, so the migration does **not** cover them.
Repopulate via their HTTP ingest routes (`POST /v2/indices/<provider>/ingest`),
not the migration:

- `github_organizations`, `github_users`
- `huggingface_users`, `huggingface_papers`
- `dockerhub`

## 2. `zenodo_communities` vs legacy `communities` (finding #5)

The migration covers `zenodo_records` but **not** communities. The live,
populated store is the legacy `data/index/communities/communities.duckdb`
(469 rows, all `source='zenodo'`); the split `zenodo_communities` store is
**not built**. Until a `communities → zenodo_communities` migration is wired,
**`communities` remains the live store** — point community lookups there, and do
not expect `zenodo_communities` to be populated.

## 3. Worker concurrency — match the client to `EXTRACTOR_WORKERS` (finding #9)

GME serves with a small worker count (gunicorn `WORKERS`, default 2; some
deployments run 1 on purpose to avoid DuckDB RW-lock contention across workers).
Each `/v2/extract` or `/v2/indices/<p>/ingest` call occupies a worker for the
duration of the job.

**Match client concurrency to the worker count.** Fanning out 5–6 concurrent
calls against a single worker saturates it: the extra calls queue, poll requests
get `[Errno 104] Connection reset by peer`, and throughput does not improve
(they retry and eventually succeed, but the log is noisy and it is *slower* than
serial). Run client concurrency ≈ `WORKERS`; serial is both cleaner and, at
`WORKERS=1`, faster.

## 4. Field coverage: `rule_based` vs `llm` runtime (finding #7)

`agent_runtime` selects which agent fills the entity. Some fields are only
populated under `llm` because they need an LLM tool/reasoning step the
rule-based agent has no equivalent for:

| Field | `rule_based` | `llm` |
|-------|--------------|-------|
| `pulse:githubRepositoryHandle`, core repo metadata | ✅ | ✅ |
| `pulse:ownedBy` | the GitHub owner URL/handle | owner URL/handle, optionally ROR-enriched |
| `pulse:discipline` (Wikidata QIDs) | ✅ schema-wise, but **empty in practice** — no Wikidata mapping without the LLM `list_disciplines` tool | ✅ populated |
| ROR parent enrichment of owner orgs | strict deterministic token-overlap rule; abstains unless an unambiguous winner | LLM `ror_parent` selector with geography / collision rules |

If you need Wikidata disciplines or LLM-judged ROR parents, run
`agent_runtime: "llm"` (or `"hybrid"`).

## 5. LLM runtime timeout (finding #1)

A misconfigured / unreachable LLM provider (empty key, wrong `*_BASE_URL`, dead
host) no longer hangs the worker forever — the chat call has a default
per-request timeout (120 s) so a stuck provider fails bounded instead of
occupying the single worker indefinitely. Tune with `V2_LLM_TIMEOUT_SECONDS`.

## 6. Cross-store DuckDB maintenance

One command walks every on-disk store under `$INDEX_DATA_DIR` and refreshes its
compacted read-only snapshot (the `<store>.ro.duckdb` the Hub reads):

```bash
# Health report (read-only): tables, row counts, live + snapshot sizes
python -m src.index._federated.maintenance --check

# Optimize every store: CHECKPOINT (fold WAL) + republish the compacted .ro snapshot
python -m src.index._federated.maintenance

# One store only
python -m src.index._federated.maintenance --store snsf
```

Enumeration is by disk, so it covers every store with a DuckDB file (including
unregistered ones) and skips FAISS-only stores (`ror`). It needs **exclusive
write access** per store — run it when the serving process isn't holding the
live file open. It does **not** deep-compact the live file (DuckDB has no safe
in-place VACUUM); the `.ro` snapshot is the compacted copy. Honours
`INDEX_DUCKDB_SNAPSHOT` (set falsey to disable snapshotting).

`--check` is also the quickest way to spot **legacy/stale stores** still on
disk — e.g. pre-split monoliths `github.duckdb` / `huggingface.duckdb` /
`communities.duckdb` sitting alongside the split stores — which are candidates
for retirement once consumers point at the split ones.
