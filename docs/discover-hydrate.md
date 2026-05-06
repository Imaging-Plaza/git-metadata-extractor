# Discover + Hydrate

Build up the gme RAG indices by streaming candidate identifiers
(**Seeds**) from one source into the right index's hydrator. The two
operations are protocols extending the federated layer; they sit
alongside `gme search` / `gme entity` (read-only) and provide the
write-side of cross-index work.

> **TL;DR.** ORCID already worked this way (`discover` → `seeds` table
> → `ingest`). The new layer lifts that pattern to the federated CLI
> and makes it cross-index: any discoverer can produce seeds that any
> matching hydrator consumes. Internal design: see
> [`.internal/federated/discover-hydrate-design.md`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/.internal/federated/discover-hydrate-design.md).

## Mental model

```
┌─ Discover ────────────────────────────────────────────────────────────┐
│  source (web scrape, search, citation graph, dump diff, sibling-      │
│  index extract)                                                       │
│       │                                                               │
│       ▼                                                               │
│  Seed{id, seed_type, source, hint?}   ← JSONL on stdout / --out      │
└──────┬────────────────────────────────────────────────────────────────┘
       │  pipe or file
       ▼
┌─ Hydrate ─────────────────────────────────────────────────────────────┐
│  group seeds by `seed_type`  →  route to matching hydrators           │
│       │                                                               │
│       ▼                                                               │
│  per-index fetch + upsert (idempotent, ON CONFLICT DO UPDATE/NOTHING) │
└───────────────────────────────────────────────────────────────────────┘
```

A `Seed` is a 4-tuple: `id` (canonical identifier), `seed_type`
(open string discriminator: `"doi"`, `"orcid"`, `"openalex_work"`,
`"github_repo"`, `"zenodo_id"`, …), `source` (provenance), and an
optional `hint` dict for opaque metadata that travels with the seed
(e.g. an affiliation ROR to stamp at hydration time).

## CLI

Both subcommands live under `python -m src.index._federated`
(aliased as `gme`).

### `gme discover`

Run a registered discoverer and emit Seeds as JSONL.

```bash
# Scrape SDSC's publications page → DOI seeds
gme discover --source datascience-ch --indices openalex --out sdsc-seeds.jsonl

# Generic OpenAlex /works search
gme discover --source from-search --indices openalex \
  --opt query="machine learning fairness" --out ml-seeds.jsonl

# Find works in our DB whose referenced_works are not yet populated
gme discover --source from-references --indices openalex --out missing-refs.jsonl

# ORCID multi-source seed table population (also returns a seed stream)
gme discover --source both --indices orcid --out orcid-seeds.jsonl

# GitHub: scrape the dependents network of a target repo
gme discover --source dependents --indices github \
  --opt target=Imaging-Plaza/git-metadata-extractor --out gh-seeds.jsonl
```

Source-specific options pass through `--opt key=value` (repeatable).

### `gme hydrate`

Read Seeds from a JSONL file (or stdin) and dispatch to the matching
hydrators. Idempotent: by default skips seeds whose canonical record
is already in the target store.

```bash
# Bring missing DOIs from the SDSC scrape into our DB
gme hydrate sdsc-seeds.jsonl

# Restrict to a subset of indices
gme hydrate seeds.jsonl --indices openalex,zenodo

# Force re-fetch even if already in DB
gme hydrate seeds.jsonl --all

# Pipeline form (no temp file)
gme discover --source datascience-ch --indices openalex \
  | gme hydrate -
```

The dispatcher groups seeds by `seed_type` and forwards each group to
every hydrator that accepts it. A single seed can therefore be
consumed by multiple indices (e.g. a DOI seed from datascience.ch can
feed both OpenAlex *and* Zenodo when the DOI is `10.5281/zenodo.<id>`).

### `gme indices`

Lists every registered adapter / discoverer / hydrator and what each
exposes.

```bash
gme indices
```

```jsonc
{
  "openalex": {
    "entity_types": ["works", "authors", "institutions", "sources", "topics", "concepts"],
    "search": true,
    "lookup": true,
    "discover": ["from-search", "from-references", "datascience-ch"],
    "hydrate":  ["doi", "openalex_work", "openalex_author"]
  },
  "orcid": {
    "entity_types": ["persons", "employments", "educations"],
    "search": true,
    "lookup": true,
    "discover": ["openalex", "orcid_search", "both"],
    "hydrate":  ["orcid"]
  },
  ...
}
```

## Coverage matrix

State as of 2026-05-04. Stub = surface registered, full implementation
TBD; rest are production-ready paths.

| Index | Discover sources | Hydrate seed types | Status |
|---|---|---|---|
| **openalex** | `from-search`, `from-references`, `datascience-ch` | `doi`, `openalex_work`, `openalex_author` | production |
| **orcid** | `openalex`, `orcid_search`, `both` | `orcid` | production |
| **zenodo** | `infoscience` | `zenodo_id`, `doi` | production |
| **infoscience** | `from-search` | `infoscience_url` | discover real / hydrate stub |
| **ethz_research_collection** | `from-search` | `ethz_rc_url` | discover real / hydrate stub |
| **github** | `dependents`, `from-references` | `github_repo` | discover real / hydrate stub |
| **huggingface** | `orgs`, `from-search` | `hf_model`, `hf_dataset`, `hf_space`, `hf_org` | discover (orgs) real / hydrate stub |
| **renkulab** | `from-search` | `renkulab_url` | stub |
| **snsf** | — | — | surface only |
| **swissubase** | `from-search` | `swissubase_url` | stub |
| ror | (n/a — dump-driven) | (n/a) | search/lookup only |
| epfl_graph | (n/a — dump-driven) | (n/a) | search/lookup only |

## Seed shape (JSONL)

One JSON object per line:

```json
{"id": "10.1029/2025WR042835", "seed_type": "doi", "source": "datascience.ch", "hint": {"affiliation_ror": "https://ror.org/02hdt9m26"}}
{"id": "https://openalex.org/W4400555555", "seed_type": "openalex_work", "source": "from-references", "hint": {"refs_only": true}}
{"id": "0000-0002-1825-0097", "seed_type": "orcid", "source": "orcid:openalex", "hint": {"scope": "switzerland", "discovered_via": "openalex"}}
```

The `hint` dict is opaque — each hydrator interprets keys it
recognises (e.g. OpenAlex respects `affiliation_ror` to stamp
`work_institutions`; ORCID uses `scope`) and ignores the rest.

## Common recipes

### Add SDSC publications missing from our OpenAlex DB

```bash
# 1. Scrape datascience.ch (32 pages, ~300 DOIs).
# 2. Cross-check against works.doi, fetch the missing ones,
#    stamp work_institutions with SDSC's OpenAlex institution id.
gme discover --source datascience-ch --indices openalex --out sdsc.jsonl
gme hydrate sdsc.jsonl --indices openalex
```

### Backfill OpenAlex `work_references`

For works ingested before `referenced_works` was in `WORKS_PROJECTION`
(legacy DBs):

```bash
gme discover --source from-references --indices openalex --out missing-refs.jsonl
gme hydrate missing-refs.jsonl --indices openalex
```

The OpenAlex hydrator detects `hint.refs_only=true` and runs the
batched fast path (100 IDs per request, only populates
`work_references`, no work upsert).

For works whose `raw.referenced_works` *is* populated (newer
ingests), you can skip the API entirely:

```bash
python -m src.index.openalex.cli references-extract
```

### Cross-source dedup via JSONL ops

Since seeds are JSONL, standard CLI tools work:

```bash
# Union DOIs from two sources, dedup by id
cat sdsc.jsonl ml.jsonl | jq -c 'select(.seed_type=="doi")' | sort -u > all-dois.jsonl
gme hydrate all-dois.jsonl --indices openalex
```

## Adding a new index

1. Create `src/index/<name>/_federated.py`.
2. Implement an `IndexDiscoverer` (declare `name`, `accepted_sources`,
   `discover(source, **opts) → Iterator[Seed]`) and/or an
   `IndexHydrator` (declare `name`, `accepted_seed_types`,
   `hydrate(seeds, only_unfetched) → HydrationSummary`).
3. Call `register_discoverer(...)` / `register_hydrator(...)` at module
   top level.
4. Add `<name>` to the `_CANDIDATES` list in
   `src/index/_federated/dh_registry.py` so `gme indices` picks it up.
5. Done — `gme discover` and `gme hydrate` route automatically based on
   `accepted_sources` / `accepted_seed_types`.

See `src/index/openalex/_federated.py` for a production reference
implementation.

## Related documentation

- [`federated-search.md`](federated-search.md) — `gme search` /
  `gme entity` / `gme indices` (read-side companion).
- [`openalex-index.md`](openalex-index.md) — full OpenAlex doc with
  the `references-extract` API-free path.
- [`rag-indices.md`](rag-indices.md) — high-level overview of all
  twelve indices.
- `.internal/federated/discover-hydrate-design.md` — design rationale,
  open questions, migration plan.
