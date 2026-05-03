# Federated Search & Cross-Index Lookup

Single-entry-point access to all gme RAG indices.
Lives at `src/index/_federated/`.

> **Two views, one corpus.** Today the project has twelve independent
> indices (HuggingFace, OpenAlex, Infoscience, ORCID, ROR, Zenodo, ETH
> Research Collection, GitHub, SNSF, RenkuLab, EPFL Graph, SWISSUbase),
> each with its own CLI / FastAPI / Qdrant collections. The federated
> layer **does not replace them** — it sits *above* them, fans out
> queries in parallel, merges results by score, and exposes a single
> `gme search` / `gme entity` interface for analysts and tools (including
> the v2 LLM pipeline).

## When to reach for it

| Question | Use |
|---|---|
| "Find anything Swiss-German LLM-related across the whole corpus" | `gme search` |
| "Given this ORCID / ROR / DOI / HF slug, give me everything we have on it" | `gme entity` |
| "I'm scripting against just one index and know which one" | the per-index CLI (`hf-search`, `orcid-search`, …) |
| "What indices are loaded right now?" | `gme indices` |

## Architecture (1-paragraph version)

Each per-index module gets a thin **adapter** under
`src/index/_federated/adapters/<name>.py` that implements a 2-method
`IndexAdapter` Protocol — `search()` and `lookup()`. Adapters self-register
on import via `register(adapter)`. The federated layer fans out across all
registered adapters in a `ThreadPoolExecutor`, merges hits by `score`, and
returns a single JSON shape. Adapters never share state — a failure in one
index is logged and skipped, the rest continue.

```
                  ┌─────────────────────────────────────────────┐
   user query ─►  │     gme search / gme entity (CLI / API)    │
                  └────────────────────┬────────────────────────┘
                                       │
                       ThreadPoolExecutor — fans out in parallel
                                       │
       ┌────────┬──────────┬────────┬──────┬──────┬─────────┐
       ▼        ▼          ▼        ▼      ▼      ▼         ▼
  HF adapter   OpenAlex   Infoscience  ORCID  ROR  Zenodo  (more…)
       │        │          │        │      │      │
       └────────┴──────────┴────────┴──────┴──────┘
                                       │
                       merge by score → trim to top_k
                                       │
                                       ▼
              {"hits":[…], "by_index":{…}, "errors":{…}}
```

Each adapter is **lazy-imported** inside its methods, so spinning up the
CLI doesn't pay the cost of loading every index's heavy dependencies.

## Quick start

### Federated semantic search

```bash
# Search everything in parallel, top 20 across all indices
just gme-search "Swiss German large language model"

# Restrict to a subset of indices
just gme-search "EPFL machine learning" --indices huggingface,openalex,orcid

# Pull more candidates from each index, then trim to top-50 overall
just gme-search "remote sensing earth observation" --top-k 50 --top-k-per-index 10

# Forward filters — adapters that recognise the key apply them, others ignore
just gme-search "Swiss researcher" --filter namespace_kind=user
just gme-search "ETH paper" --filter year=2024 --filter publication_year=2024

# Restrict each adapter to a single entity type
just gme-search "Swiss German LLM" --entity-type models

# Cross-index rerank: send the merged candidate pool through RCP's
# cross-encoder once for a globally-fair ordering. Per-adapter scores
# aren't directly comparable (each ranks within its own pool); this
# fixes that. Costs +1 RCP call.
just gme-search "Swiss German LLM" --rerank
```

Output shape:

```json
{
  "hits": [
    {
      "index": "huggingface",
      "entity_type": "model",
      "id": "ZurichNLP/swissbert",
      "title": "ZurichNLP/swissbert",
      "score": 0.97,
      "summary": "ZurichNLP/swissbert — fill-mask — cc-by-nc-4.0",
      "url": "https://huggingface.co/ZurichNLP/swissbert",
      "payload": { "...": "raw qdrant payload" }
    },
    "..."
  ],
  "by_index": {"huggingface": 5, "openalex": 5, "orcid": 3, "ror": 2, "zenodo": 2, "infoscience": 3, "github": 2, "ethz_research_collection": 2, "snsf": 1},
  "errors": {},
  "registered_indices": ["ethz_research_collection", "github", "huggingface", "infoscience", "openalex", "orcid", "ror", "snsf", "zenodo"],
  "reranked": false
}
```

`reranked` is `true` when the response went through the cross-index
rerank step (`--rerank` flag); otherwise scores are per-adapter only and
not directly comparable across indices.

### Cross-index entity lookup

Pass any identifier — slug, full URL, ORCID, ROR, DOI, UUID — and every
adapter that recognises the shape returns matches.

```bash
# HF org slug → only HF returns a record (others see no match in their identifier patterns)
just gme-entity epfl-llm

# ORCID → orcid adapter resolves; the rest return 0
just gme-entity 0000-0001-9534-3870
just gme-entity https://orcid.org/0000-0001-9534-3870

# ROR
just gme-entity https://ror.org/02s376052

# OpenAlex Work / Author / Institution
just gme-entity W2741809807
just gme-entity https://openalex.org/A5023888391

# Zenodo (numeric ID, URL, or DOI)
just gme-entity 5732376
just gme-entity 10.5281/zenodo.5732376

# Infoscience UUID
just gme-entity 5f5978a4-149d-400f-a11b-1786dacea50c

# HF repo (author/repo)
just gme-entity epfl-llm/meditron-7b
just gme-entity https://huggingface.co/EPFL-VILAB/4M

# Restrict the lookup to a subset of indices
just gme-entity 0000-0001-9534-3870 --indices orcid,openalex
```

Output shape:

```json
{
  "identifier": "epfl-llm",
  "records": [
    {
      "index": "huggingface",
      "entity_type": "org",
      "id": "epfl-llm",
      "data": {"slug": "epfl-llm", "fullname": "EPFL LLM Team", "...": "..."},
      "url": "https://huggingface.co/epfl-llm"
    }
  ],
  "by_index": {"huggingface": 1, "openalex": 0, "orcid": 0, "infoscience": 0, "ror": 0, "zenodo": 0},
  "errors": {}
}
```

### List registered adapters

```bash
just gme-indices
```

```json
{
  "huggingface":  {"entity_types": ["models", "datasets", "spaces", "orgs"]},
  "infoscience": {"entity_types": ["chunks", "articles", "persons", "organizations"]},
  "openalex":    {"entity_types": ["works", "authors", "institutions", "sources", "topics", "concepts"]},
  "orcid":       {"entity_types": ["persons", "employments", "educations"]},
  "ror":         {"entity_types": ["organizations"]},
  "zenodo":      {"entity_types": ["zenodo_records"]}
}
```

## Identifier patterns recognised by `gme entity`

| Pattern | Resolved by |
|---|---|
| `<author>/<repo>` (e.g. `epfl-llm/meditron-7b`) | `huggingface`, `github` (both try; either may match) |
| `https://huggingface.co/...` URLs | `huggingface` |
| `https://github.com/<owner>/<repo>` URLs | `github` |
| Bare HF slug (e.g. `epfl-llm`) | `huggingface` (org/user) |
| `https://openalex.org/Wxxxxx` or bare `Wxxxxx`, `Axxxxx`, etc. | `openalex` |
| `0000-0001-2345-6789` or `https://orcid.org/...` | `orcid` |
| `https://ror.org/<9-char-id>` or bare 9-char ID | `ror` |
| Numeric Zenodo ID, `https://zenodo.org/record/<n>`, or `10.5281/zenodo.<n>` DOI | `zenodo` |
| UUID (or `https://infoscience.epfl.ch/.../<uuid>`) | `infoscience` |
| UUID (or `https://www.research-collection.ethz.ch/handle/<id>`) | `ethz_research_collection` |
| 6–7 digit grant id, or `https://data.snf.ch/grants/grant/<id>` | `snsf` |

When in doubt, paste the URL — every adapter's regex is conservative and
will silently no-op on inputs it doesn't recognise.

## Adding a new index

1. Implement `IndexAdapter` (Protocol with `search()` and `lookup()`):
   ```python
   from src.index._federated.registry import EntityRecord, Hit, register

   class MyAdapter:
       name = "my_index"
       entity_types = ["thing"]

       def search(self, *, query, entity_type, top_k, filters):
           # Lazy import + call your index's semantic_search
           return [Hit(index=self.name, entity_type="thing", id=..., score=..., ...)]

       def lookup(self, identifier):
           # Detect identifier shape, return [] if not recognised
           return [EntityRecord(...)]

   register(MyAdapter())
   ```
2. Save it as `src/index/_federated/adapters/my_index.py`.
3. Add `"my_index"` to the `candidates` tuple in `registry.py:load_adapters`.
4. The CLI now lists it under `gme indices`. Done.

Adapters should:
- **Lazy-import** index internals inside the methods (keeps `gme indices` cheap).
- **Catch broad exceptions** — federation must keep working even if one index is down.
- **Return empty** for unknown identifiers in `lookup()` (no exceptions for "not mine").

## Limits + open follow-ups

- **No facet aggregation across indices.** HF has `--facets` natively (`docs/huggingface-index.md`); the federated layer does not yet roll those up.
- **One identifier at a time** in `gme entity`. Batching would be a small extension if needed for scripting.
- **Errors are logged, not surfaced.** The `errors` field in the response is populated when an adapter raises, but per-adapter timeouts aren't enforced — a hung adapter could stall the response.
- **Cross-index entity dedup.** If an ORCID resolves to the same person via both the `orcid` and `openalex` adapters, you currently get two records. A canonicalisation pass would fold them. See `ROADMAP.md`.

## Related documentation

- [`huggingface-index.md`](huggingface-index.md) — direct-access guide for the HF index
- [`v2-rag-tools.md`](v2-rag-tools.md) — agent-side tools that the v2 LLM pipeline uses (per-index, the federated tool is plumbed in alongside)
