# Configuration ownership

This directory contains extraction experiment profiles and benchmark corpora. **Source-to-ontology mappings
live in [`git_metadata_extractor/mapping/config/`](../git_metadata_extractor/mapping/config/README.txt)**,
not here.

| Path | Owner and purpose | Used by |
| --- | --- | --- |
| `pipelines/*.yaml` | Extractor: collectors, agent runtime, toolsets, concurrency and optional model selection | `python -m git_metadata_extractor.experiments` |
| `benchmarks/mock.json` | Extractor: a small deterministic evaluation corpus | The same CLI's `benchmark` command |

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
The lookup flags disable optional source enrichment. Rule-based execution
makes no model calls; mock providers keep this smoke run independent of live APIs.

The other profiles compare hybrid, full LLM, repository tools, and separate
metadata/README collectors. Hybrid and LLM trials can make model calls even
with mock providers. Use `rule_based.yaml` for an offline smoke run.
Profile model overrides require `V2_ALLOW_REQUEST_MODEL_OVERRIDE=true` and
the corresponding model credentials. Increment the profile version when
changing a benchmark definition, and retain its generated manifest with results.

## Index configuration belongs to sources

All index YAML files now live in the independent sources checkout at
`dev/open-pulse-sources/config/index/`. Its container loads them and owns
DuckDB/Qdrant access, embedding, reranking, ingestion and bootstrap. The
extractor calls authenticated HTTP operations and has no index-library fallback.

Configure the extractor with `SOURCES_API_URL`, `SOURCES_API_TOKEN`, and optionally
`SOURCES_API_TIMEOUT_SECONDS` (default 30). Configure source-specific model,
collection, scope, data-directory and Qdrant settings on **sources**. Query
embedding settings must still match the indexed vectors; changing a model does
not rebuild an index automatically.

The current local sources branch provides the new retrieval and auxiliary
operations; the old published 0.1.2 image does not. See the
[cross-repo contract](../docs/cross-repo-contract.md).
