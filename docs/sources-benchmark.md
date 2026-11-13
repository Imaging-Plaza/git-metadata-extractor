# Does extraction produce a useful graph?

The corpus at `config/benchmarks/sources-20.json` exercises 20 research and general
software, institutional and individual owners, archived repositories, large
projects, and sparse metadata. The selection notes at `config/benchmarks/sources-20-notes.md`
explain each case and the independently checked identity facts.

This is a regression and investigation bench, not a gold standard for affiliation
or publication accuracy. A graph can validate and preserve its repository name
while still attaching an unrelated paper or an incorrect institution.

The [2026-11-13 live results](sources-benchmark-results-2026-11-13.md) contain all
40 rule-based/hybrid outcomes and the reviewed citation, ownership and coverage
findings.

## Run against your sources service

Use the normal development environment with GME's Python dependencies and the
pinned ontology available. Set `GME_GITHUB_TOKEN`, `SOURCES_API_URL`, and
`SOURCES_API_TOKEN` through your usual secret environment. The sources service
owns the indices; GME only calls its HTTP API. Optional model profiles also need
their configured model credential, normally `RCP_TOKEN`.

```bash
export V2_USE_MOCK_PROVIDERS=false
export V2_PIPELINE_CACHE_ENABLED=false
export V2_CANONICAL_OUTPUT_ENABLED=true
export V2_SUBSTRATE_ENABLED=true
export V2_LINK_VERACITY_ENABLED=false
export V2_EXPAND_OWNED_REPOS=false
export V2_LLM_TIMEOUT_SECONDS=120

python -m git_metadata_extractor.experiments benchmark \
  --profile config/pipelines/rule_based.yaml \
  --profile config/pipelines/hybrid.yaml \
  --corpus config/benchmarks/sources-20.json \
  --output data/benchmarks/sources-first-run \
  --providers live --timeout 600 --include-run-report

python scripts/v2/review_benchmark.py data/benchmarks/sources-first-run
```

Use a new output directory for each run. The runner preserves failed trials,
continues the matrix, and returns a nonzero exit code if any trial failed.
`--timeout` is the per-trial asynchronous timeout; synchronous provider work
may delay cancellation. Calls can consume GitHub quota and model credits.
The runner uses read-only extraction: it does not ingest indices or write
substrate graphs to the store. Run reports are optional outside this bench.

The command runs both profiles for all 20 cases: 40 trials. Omit the hybrid
profile for a rule-based-only run. For a smaller initial model
comparison, the selection notes recommend five cases; create a filtered copy
of the corpus retaining those IDs. Keep fan-out and environment settings the
same across profiles. Add `config/pipelines/llm.yaml` only when you want the
additional model workload.

## Read the artifacts

- `manifest.json`: profiles, source URLs, execution fingerprints and settings.
- `<profile>/<case>/<repeat>/result.json`: retained graph, warnings, provenance,
  optional substrate and run report.
- `trial.json`, `summary.json`, `trials.csv`: completion, timings, observed model
  usage, identity recall and validation results. Failed trials remain visible.
- `quality.json` and `quality.md`: offline structural and semantic triage,
  source observations, article titles, organization names and profile differences.

The review command can also inspect a parent directory containing several
benchmark batches. It does not make additional provider or model calls.

Review these questions separately:

1. **Identity:** is the requested repository present with the correct name and
   handle? The three expected facts per case measure this narrow contract.
2. **Structure:** did canonical SHACL validation actually run and pass? Are
   entity IDs unique and graph-internal references resolvable?
3. **Sources:** were indexed retrievals observed? A service version identifies
   a build, not an immutable index snapshot. An empty index limits coverage.
   A normally returning RAG adapter may hide a failed HTTP call, so its return
   alone is not proof that the source query succeeded or found records.
4. **Meaning:** do publications describe this project, and do affiliation
   claims have evidence? Check repository documentation and source records.
   A matching keyword, organization handle or valid DOI is insufficient.
5. **Cost:** what changed between runtimes, and was the extra latency/model
   usage justified? Concurrent batches and warm service caches affect timing.

`acceptable` means that the implemented checks raised no finding. It does not
certify factual correctness. `needs_review` includes warnings, unknown validation
or evidence coverage, and every attached article until attribution is reviewed.
`failed` denotes a failed trial or a detected structural/validation defect.

Keep raw results under ignored `data/benchmarks/`; they can contain public
personal information and provider diagnostics. Commit concise reviewed reports
and the reusable corpus, not credentials or complete provider caches.
