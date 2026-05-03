# RAG Roadmap

What's left to build across the gme RAG stack, ranked by impact / effort.
Each item links to the relevant module so you can pick one and go.

> Status as of **2026-05-02**. Re-rank after every major shipment.

## Tier 1 — quick wins, big impact (do these first)

> **Items 1, 2, 3 shipped 2026-05-02** — see "Done" section below. Items 4 and 5 are still open.

### 4. Periodic discover + ingest cron
**Effort**: ~50 LOC + a justfile recipe.
**Impact**: corpus stays fresh without manual nudging.

Use the [`/schedule`](https://github.com/Imaging-Plaza/git-metadata-extractor) routine harness already in the repo. Schedule:
- weekly `hf-discover-orgs --scope switzerland` + log review prompt
- weekly `<index>-ingest --scope switzerland` for each index (skip-cache makes it cheap)
- weekly `<index>-embed`
- monthly `gme search` smoke-test that all 9 indices respond

### 5. Skip-already-ingested in remaining indices
**Effort**: ~10 LOC each.
**Impact**: same win as the HF fix on 2026-05-01 — refreshes go from minutes to seconds.

The pattern is now in `src/index/huggingface/ingest/{models,datasets,spaces}_ingest.py`: ask the listing endpoint to expand `lastModified` + `sha`, compare against the stored `sha`, skip if unchanged. Apply to:
- `openalex/ingest/` (compare `updated_date` per work)
- `orcid/ingest/` (compare `last_modified_date`)
- `zenodo/ingest/` (compare `updated`)
- `gh/ingest/` (compare `updated_at`)
- `infoscience/discover.py` (compare `lastModified`)

## Tier 2 — quality & coverage

### 6. Per-adapter timeouts in federated layer
**Effort**: ~15 LOC.
A hung adapter currently can stall the whole `gme search` response. Wrap each `Future` with `wait(timeout=…)` and surface timeouts in the `errors` field.

### 7. Federated faceting
**Effort**: ~80 LOC.
HF has `--facets license,pipeline_tag,author` natively. Generalise: `gme search --facets license,year,country_code` rolls up keys per adapter that emits them. Useful for "what licenses dominate Swiss-German results" across indices.

### 8. Time-decay in ranking
**Effort**: ~10 LOC per index.
A 2025 model and a 2023 model rerank symmetrically today. Add a small `last_modified` boost in the rerank step (linear or exponential decay from "now"). Behind a `--fresh` flag so historical research isn't penalised.

### 9. Sparse-bio HF orgs
**Effort**: ~50 LOC.
Orgs like `mtc` (225 models, fullname just "MTC") rank poorly because their embed text is dominated by repo titles. Two paths:
- Scrape the HF org page HTML (richer than the API exposes).
- Hand-curate `details` for top sparse orgs in a `data/index/huggingface/overrides.yaml`.

### 10. Citation parsing from READMEs
**Effort**: ~120 LOC.
Most HF model cards have a `## Citation` block with bibtex / arxiv ID. Extract during ingest, store as `models.citations JSON`, and cross-link to OpenAlex/Infoscience records (DOI match). Powers "find papers that introduce this model".

### 11. Author cross-entity view
**Effort**: ~60 LOC.
A new tool/CLI: `gme-author <slug-or-orcid>` returns one consolidated card — bio, models, datasets, papers (infoscience), citing papers, ORCID employments, openalex works, ROR org. Saves agents from running 5 sequential lookups.

### 12. Hospitals / clinical AI coverage
**Effort**: 1 day quarterly.
CHUV, HUG, USZ, Inselspital, Universitätsspital Basel — verified zero HF presence today (2026-05-02). Re-check quarterly. The right pattern: `gme-discover --domain medical --scope switzerland` runs the substring search across hospital-typical tokens.

## Tier 3 — new entity types

### 13. HF Papers entity
**Effort**: ~150 LOC + a new collection.
`huggingface.co/papers/<arxiv-id>` is a real HF entity that links models ↔ papers. Index as a 5th HF entity type (`hf_papers`). Most useful as a join column between HF and OpenAlex/arXiv.

### 14. HF Collections
**Effort**: ~80 LOC.
HF "collections" are author-curated bundles (e.g. `EPFL-VILAB/4M-21B`). Worth indexing because they encode the lab's own narrative about what goes together.

### 15. arXiv adapter
**Effort**: ~200 LOC + dump pipeline.
Many EPFL papers live on arXiv before infoscience indexes them. arXiv has a clean OAI-PMH bulk API. New module `src/index/arxiv/` mirroring infoscience.

### 16. Crossref adapter
**Effort**: ~150 LOC.
For DOI → metadata resolution and citing-paper graphs. Useful as a join key between every paper-publishing index.

### 17. OpenReview adapter
**Effort**: ~150 LOC.
For ML conference papers (ICLR, NeurIPS, ICML). EPFL/ETH groups publish heavily here. Has a public API.

## Tier 4 — operations & observability

### 18. Webhook for new EPFL repos
**Effort**: ~100 LOC.
HF doesn't expose webhooks but a poll-and-diff every 6h on the seed authors would catch new releases within hours instead of days.

### 19. Cross-index dedup in `gme-entity`
**Effort**: ~40 LOC.
If an ORCID resolves to a person AND OpenAlex resolves the same person via their ORCID-link, currently both records come back independently. A canonicalisation pass would group them.

### 20. Federated faceting + dedup unified
**Effort**: ~100 LOC (depends on #7 + #19).
A `gme-search --merge-by orcid` would dedup hits across indices on a chosen identifier.

### 21. Persistent corpus stats dashboard
**Effort**: ~100 LOC.
A `gme-stats` command that runs every status check + writes to `data/stats/<date>.json`, then a Markdown report or simple HTML. Useful for "is the corpus growing".

### 22. Backfill workflow generalised
**Effort**: ~50 LOC.
HF got `hf-backfill-payloads` to retroactively push `base_model` to existing Qdrant points without re-embedding. The same pattern (push new payload fields without re-embed) is going to come up for every index. Extract a `--backfill <key>` flag in each `<index>-embed`.

### 23. uv sync auto-load .env
**Effort**: ~5 LOC.
Several modules silently miss `HF_TOKEN` / `RCP_TOKEN` if the shell predates the `.env` edit. Add `python-dotenv.load_dotenv()` at the top of each `cli.py` so the env always reflects the file.

## Coverage gaps already verified

These were probed during 2026-05-01/02 and have **no meaningful HF presence today**. Worth re-checking in 6 months but don't expect quick wins:

- **Hospitals**: CHUV, HUG, Inselspital, USZ, Universitätsspital Basel
- **Federal research centres**: PSI / Paul Scherrer, EAWAG, Empa, WSL, CSEM, RUAG
- **Supercomputing / bioinformatics**: CSCS (only personal user), SIB, Bgee, Expasy, Vital-IT
- **Other Swiss unis (HF presence)**: USI, SUPSI, IDSIA, UniL, UniFR, UniNE
- **Crypto Valley**: DFINITY, Tezos, Cardano Foundation, Casper
- **Big pharma**: Roche, Novartis, Lonza (no HF orgs)
- **Insurance / finance**: Pictet, Julius Bär, Zurich Insurance, Helvetia
- **Industrial giants**: ABB, Sensirion, Logitech, Sonova, Pix4D

The 6 confirmed Swiss companies on HF (lakera, swisscom, LatticeFlow, squirro, eraneos, ubs-ai) are already in seed.

## Done ✅ (recent shipments)

- **2026-05-01**: `chromadb` removed (unused); HuggingFace index built+ingested+embedded; `cited_by_infoscience` cross-link; entity-level dedup in HF search; `--filter` flag.
- **2026-05-01/02**: 16 high-confidence EPFL personal users + 73 ETH/Swiss researchers + ZHAW/SWE-Swiss/lakera/squirro/swisscom/LatticeFlow/eraneos/ubs-ai promoted; corpus grew 18→147 namespaces, 296→1034 models.
- **2026-05-02 morning**: HF `--filter base_model=X`, `--facets`, `hf-backfill-payloads` (retroactive payload updates without re-embed). `models_ingest`/`datasets_ingest`/`spaces_ingest` now skip unchanged repos via sha comparison.
- **2026-05-02 afternoon**: `src/index/_federated/` shipped with 6 adapters; `gme search` / `gme entity` / `gme indices` CLIs; `FederatedRagProvider` + `search_federated_rag` + `lookup_entity_federated` v2 LLM agent tools; full docs ([`federated-search.md`](federated-search.md), [`rag-indices.md`](rag-indices.md), this file).
- **2026-05-02 evening (Tier-1 sweep)**:
  - **3 missing federated adapters** — `ethz_research_collection`, `github`, `snsf`. `gme-indices` now lists 9 (was 6). Smoke test: `gme search "Swiss German LLM"` returns hits from at least 5 indices.
  - **HF base-model lineage** — `src/index/huggingface/retrieval/lineage.py` walks the `base_models` DAG up + down. New CLI: `hf-lineage <repo_id> [--depth N]`. New v2 LLM tool: `lineage_huggingface` (registered in repository agent). Verified: meditron-7b ↔ Llama-2-7b in both directions.
  - **Cross-index reranking** — new `--rerank` flag on `gme search` sends the merged candidate pool through RCP's cross-encoder once for globally-fair ordering. Reranker config borrowed from whichever per-index module loads first (HF/openalex/zenodo/orcid). Response gains `reranked: true|false`.
