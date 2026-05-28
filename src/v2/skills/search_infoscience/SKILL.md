---
name: search-infoscience
description: Semantic search over EPFL's Infoscience repository (papers, authors, EPFL labs/units). Use when README/CITATION mentions a paper or EPFL lab without a clean DOI/ROR id. Returns thin hits; the agent decides whether to ground a claim from a hit.
---

# search-infoscience

Semantic search over the EPFL Infoscience RAG index — covers papers
(chunks + articles), researchers, and EPFL organisational units.

## When to use this

Use whenever README, CITATION, or commit history mentions a paper or
EPFL lab/unit without a clean DOI or ROR id and you need the canonical
record. Stronger signal than `search-openalex` for EPFL-affiliated work.

Do NOT use for:
- Worldwide papers without an EPFL author → `search-openalex`
- Organisations outside EPFL → `search-ror`
- Persons without paper context → `search-orcid`

## Command

```
gme-search-infoscience "<query>" [--collection chunks|articles|persons|organizations] [--top-k N] [--filter K=V] [--rerank]
python -m src.v2.skills.search_infoscience "<query>" [...same flags]
```

Collections:
- `chunks` (default): paper body fragments — best for fuzzy text matches.
- `articles`: one row per paper — for precise paper-level lookups.
- `persons`: EPFL researchers.
- `organizations`: EPFL labs/units.

## Output

JSON array. Hit shape varies by collection but always includes `id`,
`score`, a `title` or `name`, and a short `snippet`. Identifiers
(`doi`, `orcid`, `ror_id`, `sciper_id`) are present when known.

## Examples

```
gme-search-infoscience "gimie metadata extraction"
gme-search-infoscience "SDSC ORD" --collection organizations --rerank
gme-search-infoscience "Christian Müller" --collection persons --top-k 5
gme-search-infoscience "imaging plaza" --filter year=2024 --rerank
```

## Notes for the executor

- The first hit's `score` ≥ ~0.6 with reranker is a strong match;
  below that, the lookup likely failed — don't over-trust it.
- `chunks` collection has the highest recall (you're matching against
  actual paper text); `articles` has the highest precision when you know
  the title.
