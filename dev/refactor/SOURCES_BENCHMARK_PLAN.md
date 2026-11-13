# Sources-backed repository benchmark

2026-11-13. Local-only execution on `feat/2026-11-refactor`.

1. Reuse the benchmark runner with 20 public repositories and conservative identity expectations.
2. Add opt-in run-report capture so live retrieval outcomes are inspectable.
3. Use the pinned sources image with existing local indices; no ingestion, graph writes or resets.
4. Run 20 rule-based and 20 hybrid trials. Keep failures and timeouts.
5. Review identity, SHACL, references, source observations, and article attribution separately. Identity recall is not semantic precision.
6. Publish a local report and reproducible commands, verify focused tests, and review the changes.

Ruling: use the recommended rule-based/all and hybrid/subset comparison while the optional runtime question remains unanswered. This bounds model spending and covers all 20 repositories deterministically.

Ruling: preserve normal contributor limits and record timings; do not silently truncate large repositories. Sources service data is mounted read-only. Existing populated ROR collections and sparse other collections limit enrichment claims.

User steering: the runtime preference arrived during execution: **rule-based and hybrid for all 20**. This supersedes the initial five-case hybrid assumption. Keep the five scheduled hybrid trials and add the other fifteen in two isolated workers. Baselines use three isolated workers; timings are observational, not controlled speed comparisons.

## Outcome

All 40 profile/case pairs are retained, with no missing or duplicate trials.
Rule-based completed 20/20; hybrid completed 18/20 and hit the 600-second trial
timeout on Astropy and SciPy. All 38 retained graphs pass canonical SHACL and
their 114 measured identity assertions. Six planned assertions remain unmeasured
because those two hybrid trials have no final graph. Model usage is incomplete
for every completed hybrid trial; zero token counters are not zero cost.

The independent review corrected omitted RAG adapter observations, unknown
evidence coverage, recursive batch aggregation and unmeasured fact display.
Twenty focused tests pass in Docker; changed Python files pass Ruff. Findings
include unsupported ANTs ownership, missing explicit citations, strict
organization contract mismatches, degraded Linux contributor coverage, absent
Zenodo community data and Infoscience rate limiting. Production extraction
behavior was held fixed during the comparison.

Detailed results are in `docs/sources-benchmark-results-2026-11-13.md`; raw local
artifacts are under ignored `data/benchmarks/sources-20261113/`. Temporary
benchmark containers were removed after execution. No source index was ingested
or reset, and the independent sources checkout and compatibility lock were not
changed.
