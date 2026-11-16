# Docker benchmark follow-up: 2026-11-16

The full 20-repository comparison completed **20/20 rule-based and 19/20 hybrid
trials**. Citations and Linux contributor coverage improved, and the suspect ANTs
owner is absent. Astropy hybrid still times out. All 39 completed graphs pass
canonical and raw SHACL, but two Linux Membership occurrences remain incorrectly
routed into metadata. Passing shapes does not establish factual correctness.

The [November 13 baseline](sources-benchmark-results-2026-11-13.md) is preserved.
No extraction code changed during this matrix, and no failed trial was replaced
by a retry with a larger budget.

## Results

| Measure | Nov 13 rule-based | Nov 16 rule-based | Nov 13 hybrid | Nov 16 hybrid |
|---|---:|---:|---:|---:|
| Completed / planned | 20/20 | 20/20 | 18/20 | 19/20 |
| Repository identity facts matched / measured | 60/60 | 60/60 | 54/54 | 57/57 |
| Canonical SHACL passes / completed | 20/20 | 20/20 | 18/18 | 19/19 |
| Raw SHACL passes / checked | Not remeasured | 20/20 | Not remeasured | 19/19 |
| Entity occurrences stranded in metadata | 35 | 1 | 55 | 1 |
| Targeted regression facts matched / measured | 27/35 | 35/35 | 22/27 | 31/31 |
| Expected primary citations present in raw / measured | 0/7 | 7/7 | 1/5 | 6/6 |
| Median completed trial, seconds | 179.37 | 159.02 | 220.02 | 193.06 |

Identity checks cover three narrow facts per repository. The separate nine-case
regression manifest covers 35 facts per runtime, including seven primary citations
and ANTs ownership. Astropy's timeout leaves three identity facts and four targeted
facts unmeasured in the current hybrid run. These denominators exclude unavailable
results; they are not claims of 100% corpus accuracy.

**Astropy hybrid failed with `TimeoutError` after 603.46 seconds**, against the
600-second asynchronous deadline. SciPy hybrid, which also timed out in the
baseline, completed in 401.70 seconds. Its model usage is incomplete because one
model activity failed.

## What improved, and what remains

- **Citations:** all seven documented primary citations appear in rule-based
  results, including the raw layer. All six measurable hybrid cases retain theirs;
  Astropy hybrid has no result to assess.
- **ANTs ownership:** both runtimes identify `https://github.com/ANTsX` as owner and
  retain DOI `10.1038/s41598-021-87564-6`. The previously suspect housing foundation
  (`03zh00e46`) is absent from both graphs. This checks this regression, not every
  affiliation attached to ANTs contributors.
- **ROR retention:** inspection of completed results found zero excluded
  organizations carrying a ROR identity. Remaining organization exclusion events
  comprise 32 identity-less rule-based stubs, 19 identity-less hybrid stubs and 26
  hybrid stubs carrying a bare GitHub handle. These are occurrences, not distinct
  organizations; absence of a rejected ROR does not prove every affiliation is true.
- **Linux coverage:** rule-based now returns 85 entities and hybrid 87, each with
  25 people, compared with the baseline's minimal five-entity graph. Both explicitly
  warn that 26 linked accounts came from a recent-commit sample after GitHub refused
  the oversized contributor listing. This remains partial contributor coverage;
  complete historical contribution counts are unavailable.
- **Source routing:** stranded entity occurrences fell from 90 across 38 completed
  baseline runs to two across 39 current runs. Both remaining occurrences are the
  same Linux Membership, once per runtime. Strict email validation rejects a
  sampled person with a long top-level domain; dependent cleanup leaves their
  membership behind, and substrate routing puts it into the metadata graph.

The automated quality review classifies 39 results as `needs_review` and one as
`failed`. That is a triage outcome, not an assertion that all 39 graphs are wrong
or that their enrichment has been independently verified.

## Source availability and model usage

All 841 recorded responses from the local sources HTTP service were 200: 689 ROR
searches, 46 Zenodo community lookups, 45 federated searches, 41 EPFL Graph searches,
16 Infoscience retrieval searches, three Infoscience acronym lookups and one health
check. The aggregate includes preflight and failed-trial work; one Zenodo lookup
was preflight. It establishes runtime endpoint use, not nonempty matches or factual
benefit. The baseline had 105 Zenodo HTTP 503 responses.

Direct upstream calls remain a separate limitation. Ten completed runs report an
Infoscience HTTP 405 and 13 report HTTP 429; these groups can overlap, and cooldown
warnings add further unavailable calls. The cause of 405 remains unconfirmed.
Rate limiting is process-local, so concurrent trial processes do not share one
upstream request budget. GitHub package lookups also report missing `read:packages`
permission; token scopes were not changed.

Rule-based made **zero model calls**. The 19 completed hybrid runs recorded 846
model activities, 1,124 model requests, **3,918,026 prompt tokens and 197,983 completion
tokens**. Usage is complete for 18 of those 19 runs. These are observed lower-bound
totals: SciPy has one failed model activity, Astropy's timed-out work has no final
usage record, and the separate 95-token preflight is excluded. Baseline hybrid token
usage was unknown; recorded zeroes there cannot support a cost comparison.

## Next fixes

1. Accept valid long email top-level domains and remove memberships whose person
   was excluded; add a regression for routing and dependent cleanup together.
2. Normalize hybrid organization handles before strict validation. Bare handles
   currently violate the expected GitHub URL shape.
3. Diagnose Infoscience 405 responses and coordinate rate limits across workers;
   retain explicit warnings when enrichment is unavailable.
4. Trace Astropy's slow stages and bound their work before a separately recorded
   rerun. Keep this timeout in the benchmark history.

## Reproduction and limits

All service execution, extraction trials and graph validation ran in Docker.
The host only orchestrated containers and collected artifacts. The five local
services were GME, sources, Qdrant, GIMIE and Selenium. Existing real indices were
used; no ingestion or substrate-store writes occurred.

- GME revision: `1d2a0bd9eed7a6776c869f25435c84f835c9ff95`.
- Sources revision: `0de3862ba8c73a4730b70339ba1ebcc414cc0962`, version `0.2.0.dev2`.
- Corpus: `config/benchmarks/sources-20.json`; targeted checks:
  `config/benchmarks/enrichment-regression.json`.
- Profiles: `rule_based.yaml` and `hybrid.yaml`, retaining agent fan-out 2 and 4.
  Two trial processes per runtime ran concurrently, sharing upstream services.
- Model: `openai/gpt-oss-120b` through the configured RCP endpoint.
- Each trial used live providers, a cold local cache, disabled pipeline cache,
  canonical and substrate output, a run report and a 600-second deadline.
  Link verification and owned-repository expansion were disabled.

Trials invoked the benchmark CLI's extraction engine, not the HTTP API; this was
not an API load test. Timings are observational: concurrency, live upstream state,
service caches and explicit forwarding of Infoscience credentials and Selenium
configuration differ from the baseline orchestration. Faster medians cannot be
attributed to one code change.

Retained local evidence is under `data/benchmarks/sources-20261116`: individual
`trial.json` and `result.json` files, `comparison-audit.json`, `quality.json`,
`quality.md`, `transport-observations.json`, preflight, execution settings and image
IDs. Completeness checks confirm exactly 40 unique expected trials, no duplicates
or missing cases, and substrate plus raw validation for all 39 completed results.
The baseline remains under `data/benchmarks/sources-20261113`.
