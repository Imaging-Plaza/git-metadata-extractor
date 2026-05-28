---
name: search-ror
description: Semantic search over the ROR (Research Organization Registry) for canonical institution identifiers. Use whenever README/CITATION/AUTHORS mentions a research lab, university, or institute by name and you need its ROR id and parent-org chain. Do NOT use for individual people (use search-orcid) or for articles (use search-infoscience).
---

# search-ror

Semantic search over the ROR (Research Organization Registry) RAG index.

> Skill name uses kebab-case per the Agent Skills spec; the underlying
> Python package directory uses `search_ror` (Python identifier rules).
> Pi will emit a warning about the directory-name mismatch — that is
> expected and harmless.

## When to use this

Use this skill whenever the README, CITATION file, or repo metadata
mentions a research lab / university / institute by name and you need its
canonical ROR id and parent-organisation chain. Examples of triggering
phrases: "EPFL", "ETH Zürich", "Max Planck Institute for...", "INRIA",
"Idiap Research Institute".

Do **not** use this for individual people (use `search_orcid`), for
articles (use `search_infoscience` or `search_openalex`), or for code
hosting platforms.

## Command

Two interchangeable invocations — pick whichever your environment
supports:

```
# Console script (after `pip install -e .` or in the production image):
gme-search-ror "<query>" [--scope <scope>] [--top-k <n>] [--country <CC>] [--rerank]

# Module path (always works as long as the project root is on PYTHONPATH):
python -m src.v2.skills.search_ror "<query>" [--scope <scope>] [--top-k <n>] [--country <CC>] [--rerank]
```

| Arg | Default | Notes |
|---|---|---|
| `query` (positional) | required | Free-text. Institution / lab / university name. |
| `--scope` | `worldwide` | One of `worldwide`, `europe`, `switzerland`, `epfl_ethz`. Narrower scopes are faster and reduce false positives. Pick `epfl_ethz` only when context strongly implies EPFL/ETHZ neighbourhood. |
| `--top-k` | `10` | Hits to return. 5–20 is the useful range. |
| `--country` | unset | ISO 3166-1 alpha-2 (e.g. `CH`, `FR`). Filters at the store layer; only `country_code` is supported. |
| `--rerank` | off | Cross-encoder rerank over `name + types + parent + website`. Slower (~1s) but improves precision; turn on when the top-1 of a non-reranked hit list is wrong. |

## Output

A JSON array of hit objects on stdout. Each hit shape:

```json
{
  "ror_id": "https://ror.org/02s376052",
  "name": "École Polytechnique Fédérale de Lausanne",
  "country_code": "CH",
  "types": ["Education", "Funder"],
  "score": 0.91,
  "snippet": "EPFL — name + types + parent + website preview"
}
```

On failure: a JSON `{"error": "...", "kind": "..."}` on stderr and a
non-zero exit. Common `kind` values: `provider_unavailable` (RAG index
not reachable), `skill_error` (other internal error).

## Examples

Find EPFL, narrowest scope:

```
gme-search-ror "EPFL" --scope epfl_ethz --top-k 5
```

Find INRIA worldwide with reranking:

```
gme-search-ror "INRIA" --top-k 5 --rerank
```

Find Swiss institutions matching a fuzzy query:

```
gme-search-ror "computational biology Lausanne" --country CH --top-k 10 --rerank
```

## Notes for the executor

- Pick the **narrowest scope that still contains the target**. `worldwide`
  has higher recall but more false positives.
- If the top hit's `score` is below ~0.6 and you have no reranking, retry
  with `--rerank`. If still low, the institution may not be in ROR — emit
  a `urn:pulse:<uuid>` placeholder rather than guessing.
- `country_code` filtering is a hard constraint at the store, not a soft
  bias — if you pass `CH` and the target is actually French, you will get
  zero hits.
