# GME operations runbook

> **Repo split (2026-07-02):** the RAG index layer now lives in
> [open-pulse-sources](https://github.com/sdsc-ordes/open-pulse-sources).
> Index ops commands below run from that repo (same `data/index/` volume);
> the `open_pulse_sources.*` modules come from its installed library.

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
python scripts/migrate_monolith_to_split.py  # (now in the open-pulse-sources repo)

# copy rows + rebuild Qdrant + republish the .ro.duckdb snapshot
python scripts/migrate_monolith_to_split.py  # (now in the open-pulse-sources repo) --apply --reembed
```

- The orphan monolithic files are opened **read-only** and never modified — they
  remain the canonical backup.
- `--reembed` drops each store's `chunks` table + Qdrant collection and rebuilds
  from scratch (use it after a bulk copy; plain `--embed` only embeds rows with
  no existing `chunks` bookkeeping and will no-op on freshly-copied rows).
- The migration now republishes the `<store>.ro.duckdb` snapshot the Hub reads
  from. **If you mutate a store by any other path, republish the snapshot** or
  the Hub keeps serving stale data:
  `python -c "from open_pulse_sources.index._snapshot import publish_snapshot; ..."`.

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

### Bulk ingest vs. extraction isolation (Bug 04)

Bulk `/v2/indices/<p>/ingest` and interactive `/v2/extract` share one process.
The heavy ingest steps (embed pass, WAL checkpoint, `.ro` snapshot, gitlab
ingest/embed) now run on a **bounded, ingest-only thread pool**
(`V2_INGEST_MAX_THREADS`, default 2) so they can no longer saturate the default
thread pool that extraction offloads onto. This kills the dominant
ingest-starves-extraction axis.

It does **not** fully isolate the event loop or the single SQLite cache writer
lock, so on a 1–2 worker deployment you should still **avoid running large bulk
ingests concurrently with latency-sensitive extraction** — schedule ingest in a
maintenance window or against a separate API replica. (The durable fix is a
separate `gme-ingest-worker` process — see `dev/bug-plans/04`.)

Note: the lighter per-entity ingest *fetch* loops (`_ingest_one_*`) still use the
default pool; they are sequential per job and far lighter than the embed pass,
so they were left as a follow-up — route them through `run_in_ingest_pool` too
if profiling shows residual fetch-side contention.

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
python -m open_pulse_sources.index._federated.maintenance --check

# Optimize every store: CHECKPOINT (fold WAL) + republish the compacted .ro snapshot
python -m open_pulse_sources.index._federated.maintenance

# One store only
python -m open_pulse_sources.index._federated.maintenance --store snsf
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

## 7. Deploy-time index bootstrap

The serving image bootstraps every index DuckDB store **at startup** so the
stores exist (with their schema) before the first request. The Gunicorn
`on_starting` hook (`tools/config/gunicorn_conf.py`) calls
`open_pulse_sources.index._federated.bootstrap.bootstrap_all()` **once in the master process,
before any worker forks** — so no two workers race to create the same file.

- **Idempotent** — existing stores are left untouched; only missing ones are
  created. Safe to run on every restart.
- **Best-effort** — a bootstrap failure is logged (`index bootstrap on start
  failed: …`) but never blocks the server from coming up. Per-store failures
  are reported as `… store(s) not ready: {…}`.
- **Auto-discovery** — every store under `open_pulse_sources/index/* (open-pulse-sources repo)` is picked up, so newly
  added indices (e.g. the GitLab family) are bootstrapped with no extra wiring.

| Env | Default | Purpose |
|---|---|---|
| `INDEX_BOOTSTRAP_ON_START` | `true` | Set `false`/`0`/`no`/`off` to skip the startup bootstrap — e.g. when an init-container or a separate job provisions `$INDEX_DATA_DIR`. |

Run the same thing by hand (local dev, CI, or to re-create a deleted store):

```bash
make bootstrap-index                          # all stores, idempotent
python -m open_pulse_sources.index._federated.bootstrap      # same thing
python -m open_pulse_sources.index._federated.bootstrap --only gitlab_epfl_users
```

Bootstrap only **creates empty schema'd stores** — it does not ingest or embed.
Populate a store with its ingest/embed CLI or the
`POST /v2/indices/<name>/ingest` endpoint.

## 8. GIMIE now runs as a sidecar — `GIMIE_API_URL` is required

The heavy `gimie` Python dependency (and its `calamus`/`marshmallow` chain,
which hard-pinned vulnerable `python-dotenv`/`marshmallow`) was **removed from
the image**. GIMIE metadata is now fetched from the **`gimie-api` sidecar**
over HTTP.

> **2026-07-14 — the sidecar image is now GME-maintained** (`tools/gimie-api/`,
> built by both compose files as `gme-gimie-api:0.7.2`). The upstream
> `ghcr.io/sdsc-ordes/gimie-api` images (pinned digest AND `:latest`) ship
> gimie 0.6.1 with an app written for the 0.7.x API — every extraction
> request fails and the error contract collapses to an empty payload, i.e.
> **silent** loss of all gimie metadata (descriptions, contributors → no
> Person/Membership/Contribution entities). Full analysis:
> `dev/split-rag-indices/11-gimie-sidecar-jsonld-broken.md`.

**Required for every deployment that extracts repositories:**

1. Run the sidecar alongside the API (same network). It listens on `:15400`.
   The gimie library reads **`GITHUB_TOKEN`** — a **single PAT**; if your
   `GME_GITHUB_TOKEN` is a comma-separated pool, set `GIMIE_ACCESS_TOKEN`
   to one PAT from it (the compose files wire this; the GME-maintained app
   also normalizes pools itself, first token wins).
2. Set **`GIMIE_API_URL`** on the API process, e.g.
   `GIMIE_API_URL=http://gme-gimie-api:15400`.

Behaviour:

- `GIMIE_API_URL` **set** → all GIMIE extraction goes to the sidecar; on sidecar
  failure the call returns `None` and the pipeline degrades to non-GIMIE
  providers (it already tolerates an empty GIMIE graph).
- `GIMIE_API_URL` **unset** → falls back to in-process gimie, which is **no
  longer installed** in the image → a clear `RuntimeError` is raised when
  extraction is attempted. (For local in-process use: `pip install gimie==0.7.2`.)
- The client requests the sidecar's `/gimie/ttl/` route and converts to
  JSON-LD with rdflib (byte-compatible with the historical in-process
  serialization).
- Tunables: `GIMIE_API_TIMEOUT_SECONDS` (default 180).

Validate a new sidecar image with `scripts/v2/gimie_api_parity.py` — and be
aware it previously passed while the live route 404'd; extending it to fail
on degrade-to-None is tracked in task brief 11.

Both stacks in this repo wire it already:

- **Dev:** `.devcontainer/docker-compose.yml` (the `gme-gimie-api` service +
  `GIMIE_API_URL` on the devcontainer).
- **Production:** **`tools/deploy/docker-compose.yml`** — a ready-to-run stack
  (gme-api + gme-gimie-api + qdrant + selenium, image digest-pinned, persistent
  volumes):

  ```bash
  docker compose -f tools/deploy/docker-compose.yml --env-file .env up -d
  ```

  Override the app image with `GME_IMAGE` (defaults to
  `ghcr.io/imaging-plaza/git-metadata-extractor:latest`) or uncomment its
  `build:` block to build locally.

(For k8s: a `gimie-api` Deployment + Service on `:15400` with `ACCESS_TOKEN`
from the GitHub-token secret, and `GIMIE_API_URL=http://gimie-api:15400` on the
API Deployment.)
