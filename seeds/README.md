# Re-ingest seed lists

Each `<provider>.txt` in this directory is the source-of-truth list of
identifiers fed back into the corresponding `POST /v2/indices/<provider>/ingest`
endpoint by `scripts/v2/reingest_indices.py`.

## File format

- **One identifier per line.** Whitespace around the value is trimmed.
- **`#` starts a comment.** Anything from `#` to end-of-line is ignored —
  useful for grouping/labelling: e.g. `# --- EPFL labs ---` or
  `huggingface/transformers  # canonical seed`.
- **Blank lines are skipped.**
- The script de-duplicates IDs before posting (preserving first-seen order).

Empty files are valid — the script just skips ingest for that provider
with a clear log line.

## Per-provider ID shape

The shape the corresponding `*IngestRequest` schema in
`src/v2/api_models/contracts.py` expects.

| File                                  | Shape                                                                       |
|---------------------------------------|-----------------------------------------------------------------------------|
| `zenodo_records.txt`                  | numeric id, DOI (`10.5281/zenodo.…`), or full Zenodo URL                    |
| `huggingface_models.txt`              | `namespace/name`                                                            |
| `huggingface_datasets.txt`            | `namespace/name`                                                            |
| `huggingface_spaces.txt`              | `namespace/name`                                                            |
| `huggingface_users.txt`               | bare HF user slug (no URL)                                                  |
| `huggingface_organizations.txt`       | bare HF org slug (no URL)                                                   |
| `huggingface_papers.txt`              | arXiv id, versioned id, arXiv URL, HF Papers URL, `arxiv:<id>` tag, or DOI  |
| `github_repos.txt`                    | `owner/name`                                                                |
| `github_users.txt`                    | bare GitHub login                                                           |
| `github_organizations.txt`            | bare GitHub org handle                                                      |
| `openalex.txt`                        | `W…` id, `https://openalex.org/W…` URL, or DOI                              |
| `orcid.txt`                           | `XXXX-XXXX-XXXX-XXXX`                                                       |
| `renkulab.txt`                        | Renku v2 project slug or UUID                                               |
| `swissubase.txt`                      | numeric study id                                                            |
| `ethz_research_collection.txt`        | DSpace item UUID                                                            |
| `oamonitor.txt`                       | `<entity>:<id>` where entity is one of `journals`, `publications`, `publishers`, `organisations` |

The five reset-only providers (`zenodo_communities`, `ror`, `infoscience`,
`snsf`, `epfl_graph`) don't have HTTP ingest endpoints, so no seed file
is needed for them — `reingest_indices.py` will reset them and skip the
ingest step with an explanatory log line.

## Usage

```bash
# Cold-start every provider with a seed file (DuckDB + Qdrant + provider cache)
API_TOKEN=… python scripts/v2/reingest_indices.py --base-url http://localhost:8000 --wait

# Only re-ingest one provider, skip reset
python scripts/v2/reingest_indices.py --providers huggingface_models --no-reset

# Dry-run: print what would happen
python scripts/v2/reingest_indices.py --dry-run
```

See `scripts/v2/reingest_indices.py --help` for the full flag list.
