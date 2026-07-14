---
name: search-orcid
description: Semantic search over the ORCID registry (persons, employments, educations). Use to ground a contributor by name to an ORCID iD, or to look up their employment/education history. Do NOT use for orgs (use search-ror) or for articles (use search-infoscience or search-openalex).
---

# search-orcid

Semantic search over the ORCID RAG index, scoped to the configured
region (typically EPFL and adjacent Swiss institutions).

## When to use this

Use whenever README, CITATION, or AUTHORS surfaces a person's name and
you need their ORCID iD — or when you need to discover their
employment / education history to disambiguate identity.

Do NOT use for:
- Organisations → use `search-ror`
- Articles → use `search-infoscience` (EPFL) or `search-openalex` (worldwide)

## Command

```
gme-search-orcid "<query>" [--entity-type <type>] [--top-k <n>] [--filter K=V] [--rerank]
python -m git_metadata_extractor.experimental.skills.search_orcid "<query>" [...same flags]
```

| Arg | Default | Notes |
|---|---|---|
| `query` (positional) | required | Free-text. Person name, biography fragment, lab + role. |
| `--entity-type` | `persons` | One of `persons`, `employments`, `educations`. |
| `--top-k` | `10` | Hits to return. 5–20 is the useful range. |
| `--filter` | unset | Repeatable `KEY=VALUE`. Allowlisted keys only: `orcid_id`, `in_scope`, `discovered_via`, `org_ror`, `organization`, `department`, `role`. |
| `--rerank` | off | Cross-encoder rerank. Slower (~1s) but improves precision when top-1 score is borderline. |

## Output

JSON array on stdout. For `persons` entity type, hits look like:

```json
{
  "orcid_id": "https://orcid.org/0000-0002-1825-0097",
  "names": ["Carberry, Josiah"],
  "biography_snippet": "...",
  "score": 0.81
}
```

For `employments` / `educations` hits include `organization`, `role`,
`department`, year ranges.

On failure: `{"error": "...", "kind": "..."}` on stderr, non-zero exit.

## Examples

```
gme-search-orcid "Anna Smith EPFL data science"
gme-search-orcid "Marc Levrat" --rerank
gme-search-orcid "PhD machine learning" --entity-type educations --top-k 5
```

## Notes for the executor

- The first hit's `score` ≥ ~0.7 with reranker on is a strong match;
  below ~0.5 the answer is probably wrong — fall back to
  `https://github.com/<login>` rather than guessing an ORCID.
- `in_scope=true` filter narrows to the configured EPFL-adjacent set
  if you know the person is local.
