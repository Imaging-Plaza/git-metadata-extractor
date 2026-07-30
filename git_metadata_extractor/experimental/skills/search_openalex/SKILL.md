---
name: search-openalex
description: Semantic search over the OpenAlex worldwide scholarly graph (works, authors, institutions, sources, topics, concepts). Use when an entity is NOT in EPFL Infoscience and you need worldwide ground truth (DOIs, ORCIDs, ROR ids, etc.).
---

# search-openalex

Semantic search over OpenAlex — a free, worldwide scholarly knowledge
graph. Covers papers, authors, institutions, journals, topics.

## When to use this

Use whenever you need to ground a paper, author, or institution that
isn't in EPFL Infoscience (i.e. non-EPFL work). Strongest fallback for
worldwide DOIs, ORCIDs, and ROR ids.

Do NOT use for:
- EPFL papers / EPFL labs → prefer `search-infoscience` (richer local data)
- Pure organisational lookup without paper context → `search-ror`
- Person without paper context → `search-orcid`

## Command

```
gme-search-openalex "<query>" [--collection works|authors|institutions|sources|topics|concepts] [--top-k N] [--filter K=V] [--rerank]
python -m git_metadata_extractor.experimental.skills.search_openalex "<query>" [...same flags]
```

Collections:
- `works` (default): papers/preprints/datasets — abstract is indexed.
- `authors`: researchers (worldwide).
- `institutions`: research orgs with ROR ids.
- `sources`: journals/conferences.
- `topics`, `concepts`: subject classifications.

## Output

JSON array. Hit shape varies by collection. Common fields: `openalex_id`,
`title` or `display_name`, identifiers (`doi`, `orcid`, `ror`), `score`,
short `snippet` (~320 chars of abstract for works).

## Examples

```
gme-search-openalex "AlphaFold 2 protein structure prediction" --rerank
gme-search-openalex "INRIA" --collection institutions --top-k 3
gme-search-openalex "Yann LeCun" --collection authors --rerank
gme-search-openalex "imaging biomarkers" --filter year=2024 --top-k 10
```

## Notes for the executor

- Range filters: pass `year=2023` for exact year, or `--filter year=$gte:2020` is NOT supported here — use scalar matches.
- `country_code` filter on institutions narrows by country (`CH`, `FR`, etc.).
- For ambiguous worldwide searches, ALWAYS turn on `--rerank` — the
  worldwide index has many near-duplicates and the cross-encoder
  meaningfully tightens precision.
