# Configuration ownership

This directory contains extraction experiment profiles and compatibility
configuration for the separate sources library. **Source-to-ontology mappings
live in [`git_metadata_extractor/mapping/config/`](../git_metadata_extractor/mapping/config/README.txt)**,
not here.

| Path | Owner and purpose | Used by |
| --- | --- | --- |
| `pipelines/*.yaml` | Extractor: collectors, agent runtime, toolsets, concurrency and optional model selection | `python -m git_metadata_extractor.experiments` |
| `benchmarks/mock.json` | Extractor: a small deterministic evaluation corpus | The same CLI's `benchmark` command |
| `index/*.yaml` | Sources: index layouts, query embedding/reranking, scopes and source-specific settings | The pinned `open_pulse_sources` library, including local RAG readers and optional store bootstrap |

API, provider and pipeline feature flags are configured through environment
variables; see [environment setup](../docs/getting-started.md#2-configure-environment).
These YAML files do not contain credentials. Set secrets in the environment;
do not add them to profiles or index files.

## Extraction profiles and benchmarks

The profiles are validated by `experiments.models.PipelineProfile` and the
component catalog. They are selected explicitly by the experiments CLI; editing
a profile does not change the running API's default pipeline.

```bash
# Validate without provider or model calls.
python -m git_metadata_extractor.experiments validate config/pipelines/rule_based.yaml
python -m git_metadata_extractor.experiments list

# Disable post-processing index lookups for an offline rule-based smoke run.
export V2_ROR_RAG_ENABLED=false
export V2_EPFL_GRAPH_RAG_ENABLED=false
export V2_CONCEPT_TAGGING_ENABLED=false
export V2_QUERY_LOG_DIR=data/benchmarks/query-logs
# Use a new output directory each time.
python -m git_metadata_extractor.experiments benchmark --profile config/pipelines/rule_based.yaml --corpus config/benchmarks/mock.json --providers mock --output data/benchmarks/rule-based-demo
```

In PowerShell, set variables with `$env:V2_ROR_RAG_ENABLED = "false"` (and
likewise for the other variables), then run the same Python command.
Mock providers alone do not isolate the whole post-processing chain: ROR
resolution and discipline tagging can construct local index readers directly.

The other profiles compare hybrid, full LLM, repository tools, and separate
metadata/README collectors. Hybrid and LLM trials can make model calls even
with mock providers. Use `rule_based.yaml` for an offline smoke run.
Profile model overrides require `V2_ALLOW_REQUEST_MODEL_OVERRIDE=true` and
the corresponding model credentials. Increment the profile version when
changing a benchmark definition, and retain its generated manifest with results.

## Why index configuration is still here

Index ingestion, embedding and reset belong to
[`open-pulse-sources`](https://github.com/sdsc-ordes/open-pulse-sources).
However, this extractor still imports that library for local retrieval.
Most loaders in the current pinned release read `config/index/<source>.yaml`
relative to the working directory, and its Python package does not bundle
these YAML files. Docker therefore copies them into `/app/config`.
Deleting or moving them can disable local RAG providers or change their defaults.

Treat these files as compatibility copies of the sources configuration:

- Review them against the sources release whenever the dependency is upgraded.
  The [cross-repo contract](../docs/cross-repo-contract.md) defines the library
  and container version pin; this directory does not define another version.
- Keep query embeddings compatible with the indexed vectors: model, dimension,
  query instruction, collection names and scope matter on both sides. Changing
  an embedding model here does not rebuild the index.
- Prefer the supported environment overrides for deployment addresses, tokens,
  scopes and data directories. YAML also includes ingest settings because the
  current library uses one configuration model for reads and writes.
- `zenodo_communities.yaml` is the parent-organization seed map for the sources
  build command, not a retrieval provider config. It remains part of the
  compatibility set used with the pinned sources checkout.
- The SNSF loader is an exception to the working-directory rule: its default
  path is relative to the installed library, with model defaults when absent.
  It supports an explicit file path; editing this checkout's `snsf.yaml` alone
  does not change the extractor's default SNSF reader configuration.

`SOURCES_API_URL` and `SOURCES_API_TOKEN` enable remote indexed search through
`providers/sources_service.py`; `SOURCES_API_TIMEOUT_SECONDS` defaults to 30.
That bypasses local RAG construction in provider wiring and disables Gunicorn's
index bootstrap. It does **not** yet remove every library dependency: optional
auto-ingest and discipline tagging still contain local index paths. Do not
remove all index config merely because remote search is enabled.

The end state is to package source defaults in `open-pulse-sources` and finish
routing index operations through that service. Then the extractor can retain
only service connection settings and its own pipeline/mapping configuration.

## Removed legacy configuration

`config/v2/terminal_agent.yml` belonged to the removed terminal-agent PoC;
no tracked runtime or script loads it. `index/huggingface.yaml` belonged to the
removed combined index; the current library uses separate model, dataset,
space, paper, user and organization configs. Their history remains in Git.
