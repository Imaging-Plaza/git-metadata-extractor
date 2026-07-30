# Concept tagging pipeline stage

Optional stage that stamps three underscore-prefixed metadata fields onto
the root repository entity, derived from the README:

- `_keywords` — `list[str]` of plain keyword tokens.
- `_concepts` — `list[dict]` of structured concepts, each shaped
  `{label, wikipedia_url, wikidata_id, concept_id, score, source}`.
- `_disciplines` — `list[dict]` of EPFL Graph academic-discipline
  matches, each shaped `{category_id, name, score, rank, from_concept,
  source, graphsearch_url, wikipedia_url, wikipedia_page_id, chain,
  publications?, people?, units?, openalex_topics?}`.

All three fields are stripped before strict JSON-Schema validation
(handled by `schema_validation.py`: any `_`-prefixed key is dropped) and
before JSON-LD output (handled by `jsonld_build.py`: same rule). They
are purely **internal pipeline metadata**, not part of the public Open
Pulse Ontology surface (yet) — surface them by adding the corresponding
ontology terms (`schema:keywords`, `schema:about`, etc.) to the strict
schema and projecting in the output stage when the schema lands.

## When the stage runs

Right after `infer_org_units`, immediately before `build_jsonld_output`.
Operates on `assembled_output.root_entity` and only when the input is a
**repository** (skipped for user/organization extracts). Independent of
`agent_runtime` — runs the same in `llm` and `rule_based` modes.

## Backends

Pluggable via `V2_CONCEPT_TAGGING_BACKEND`:

| Backend | Source | Requirements | Quality | Speed |
|---|---|---|---|---|
| `epfl_graph` (default) | graphai.epfl.ch `/text/wikify` + `/text/keywords` | `EPFL_GRAPH_USERNAME` + `EPFL_GRAPH_PASSWORD` | high — wikified concepts with mixed-score, EPFL ontology disciplines via `/ontology/nearest_neighbor/concept/category` | ~3-8 s per README |
| `wikipedia` | MediaWiki opensearch | none | low — token-frequency heuristic feeds an unranked first-page-hit lookup | ~5 s per README (top-N requests) |
| `llm` | `pydantic-ai` Agent reusing the project's `model_config` | one of `RCP_TOKEN`/`OPENAI_API_KEY`/`OPENROUTER_API_KEY` | medium — depends on the configured model; consumes LLM tokens | ~5-15 s per README |

The whole stage is **opt-in** because each backend has its own external
dependency.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `V2_CONCEPT_TAGGING_ENABLED` | `false` | master switch — set to `true` to run the stage |
| `V2_CONCEPT_TAGGING_BACKEND` | `epfl_graph` | one of `epfl_graph` / `wikipedia` / `llm` |
| `V2_CONCEPT_TAGGING_EPFL_MIN_SCORE` | `0.0` | drop EPFL-Graph concepts whose `mixed_score` is below this threshold (0..1). Filters out low-confidence noise like "Graph theory" matched on the word "graph" |
| `V2_CONCEPT_TAGGING_OPENALEX_RELATED_ENABLED` | `false` | per top-discipline, also fetch related publications / people / units via OpenAlex (4 cheap HTTP per discipline). Requires `OPENALEX_MAILTO` for the polite pool |

Internal knobs (function arguments to `run_concept_tagging_stage` —
defaults are sensible, override only when needed):

| Argument | Default | Purpose |
|---|---|---|
| `max_keywords` | 25 | cap on `_keywords` length |
| `max_concepts` | 25 | cap on `_concepts` length |
| `max_disciplines` | 10 | cap on `_disciplines` length |
| `max_readme_chars` | 8000 | truncate long READMEs before sending to backends |
| `discipline_top_concepts` | 6 | how many top concepts to look up disciplines for |
| `discipline_top_n_per_concept` | 3 | how many disciplines to fetch per concept |
| `enrich_with_disciplines` | `true` | whether the `epfl_graph` backend also fetches disciplines via `/ontology/nearest_neighbor/concept/category` |
| `related_top_disciplines` | 3 | how many top disciplines to enrich with OpenAlex when `enable_related_openalex=True` |

## Markdown stripping

The README is normalised before being shipped to any backend — code
fences, inline code, links, images, ATX/setext headers, bold/italic
markers, list bullets, blockquotes, and HRs are all removed. Markdown
noise like `## Project Structure` and `**three-layer cache**` was the
top source of garbage keywords from the EPFL Graph keyword extractor in
v1; the stripper closes that gap.

## Discipline shape (with everything turned on)

```python
{
  "category_id": "topics-in-natural-language-processing",
  "label": "Natural language processing",
  "score": 15.7,
  "rank": 1,
  "from_concept": "Information extraction",
  "source": "epfl_graph",
  "graphsearch_url": "https://graphsearch.epfl.ch/en/category/topics-in-natural-language-processing",
  "wikipedia_url": "https://en.wikipedia.org/?curid=21652",
  "wikipedia_page_id": "21652",
  "chain": [
    {"category_id": "topics-in-natural-language-processing", "label": "Natural language processing", "depth": 0, "wikipedia_url": "..."},
    {"category_id": "natural-language-processing",            "label": "Natural language processing", "depth": 1, "wikipedia_url": "..."},
    {"category_id": "information-engineering",                "label": "Information engineering",     "depth": 2, "wikipedia_url": "..."},
    {"category_id": "applied-sciences",                       "label": "Outline of applied science",  "depth": 3, "wikipedia_url": "..."},
    {"category_id": "academic-disciplines",                   "label": "Academic disciplines",        "depth": 4, "wikipedia_url": "..."},
  ],
  # Only present when V2_CONCEPT_TAGGING_OPENALEX_RELATED_ENABLED=true:
  "openalex_topics": [
    {"topic_id": "T10395", "topic_name": "...", "score": 0.81},
    ...
  ],
  "publications": [
    {"openalex_id": "W...", "title": "...", "year": 2024, "doi": "...", "venue": "...", "url": "..."},
    ...
  ],
  "people": [
    {"openalex_id": "A...", "name": "...", "orcid": "...", "institution": "...", "ror": "..."},
    ...
  ],
  "units": [
    {"openalex_id": "I...", "name": "...", "ror": "...", "country_code": "CH", "homepage_url": "..."},
    ...
  ],
}
```

## Why not use the new disciplines RAG index?

The legacy concept_tagging path calls graphai's
`/ontology/nearest_neighbor/concept/category` once per concept (~3 HTTP
per README). Now that `open_pulse_sources/index/epfl_graph/` (in the
[open-pulse-sources](https://github.com/sdsc-ordes/open-pulse-sources) repo)
mirrors the ontology into Qdrant, the same job can be done with a single embed +
vector-search per README. **That swap is a planned follow-up** — the
behaviour change is non-trivial (different scoring, different
discipline counts) so it warrants its own review. For now the two
systems coexist:

- `concept_tagging` stage → graphai per-concept (this doc).
- `search_epfl_graph_disciplines` agent tool → Qdrant semantic search ([epfl-graph-disciplines.md](https://github.com/sdsc-ordes/open-pulse-sources/blob/main/docs/epfl-graph-disciplines.md)).

## File map

```
git_metadata_extractor/pipeline/stages/concept_tagging.py    # stage entrypoint + backends
git_metadata_extractor/pipeline/stages/__init__.py           # re-exports run_concept_tagging_stage etc.
git_metadata_extractor/api/extract.py                        # wires the stage into the v2 pipeline
.env.example                                                 # documents all the V2_CONCEPT_TAGGING_* knobs

# in the open-pulse-sources repo (installed as the open_pulse_sources library):
open_pulse_sources/module/epfl_graph/ontology.py         # /ontology/* wrappers (epfl_graph backend)
open_pulse_sources/module/epfl_graph/openalex_related.py  # publications / people / units helpers
```
