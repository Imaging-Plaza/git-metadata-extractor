# Enrichment corrections after the sources benchmark

This follow-up addresses the four findings in the [retained 20-repository
benchmark](sources-benchmark-results-2026-11-13.md). The original report and
`data/benchmarks/sources-20261113` artifacts remain the baseline. New evidence
lives locally under `data/benchmarks/enrichment-fixes-20261113`.

## Changes

- **Organization identity:** shared name and website checks reject the ANTs /
  Normalization Housing Foundation collision. A single shared subject word is
  insufficient. Conflicting websites cannot be overridden by exact-name matching;
  host comparison includes the domain suffix. Known aliases, acronyms, and
  institutional subdomains have positive regression tests.
- **Citations:** explicit preferred CFF articles, BibTeX papers, and requested
  README citations become `schema:citation` DOI/arXiv IRIs. Extraction happens
  before HTML cleanup. Publication lookup uses explicit DOI/title evidence first.
  A citation does not fabricate the authors/date needed to create an Article node.
  Commented examples, incidental bibliography references and software archive
  DOIs are excluded. Balanced DOI parentheses and arXiv eprints are preserved.
- **ROR retention:** late resolver stubs reuse organization identity
  normalization and retain a server UUID. Valid ROR organizations survive strict
  intermediate validation; the schema itself was not weakened. Raw and canonical
  projection also recover accepted ROR values from `schema:identifier` when a
  hybrid resolver keeps the original UUID to preserve inbound references.
- **Contributor coverage:** absent GIMIE identities trigger bounded GitHub REST
  pagination (three pages, at most 300 linked accounts). Unavailable or truncated
  enumeration produces warnings and a partial context-gather outcome. Those
  responses are not cached as successful complete extractions. GIMIE does not
  report pagination completeness, so nonempty GIMIE coverage remains unknown.

## Reproduce the focused comparison

`config/benchmarks/enrichment-regression.json` contains nine cases and 35 positive
assertions: repository identities, the seven primary citations, and ANTs GitHub
ownership. It deliberately leaves the original 20-case manifest unchanged.
Run it with the same providers configured as the original benchmark:

```bash
python -m git_metadata_extractor.experiments benchmark \
  --profile config/pipelines/rule_based.yaml \
  --corpus config/benchmarks/enrichment-regression.json \
  --output data/benchmarks/enrichment-recheck \
  --providers live --timeout 600 --include-run-report
```

Use a new output directory and disable the pipeline cache for a fresh run.
Inspect Linux's coverage warning and context-gather outcome separately: graph
identity assertions cannot measure contributor completeness. The ANTs negative
check is that the Japanese foundation is absent as owner or parent, not merely
that a correct owner is also present.

## Verification

The offline Docker suite passed **2,006 tests**, with 2 skipped and 3
live-credential tests deliberately deselected:

```bash
python -m pytest tests/v2 -q -k 'not real_provider_call' --disable-warnings
python -m git_metadata_extractor.mapping --check
python scripts/dev/sources_checkout.py --check
```

The test image was `gme-sources-lock-validation:20261112`, with the current
checkout mounted at `/work/gme`, `PYTHONPATH=/work/gme`, and networking disabled.
The mapping check validated **23 mappings and 3 policies**, and `mkdocs build
--strict` passed. Sources remained at
`a812bb2c5c84a5f5357cb265f50d65e85662c4d2` (`0.2.0.dev2`). New modules/tests pass
Ruff; existing files gained no additional Ruff rule findings. No schemas or
sources-service code changed.

The first broad test run exposed an existing query-log fixture leak into the
run-report tests. The fixture now restores its ContextVars on teardown; the
previously failing two-module sequence and the full suite pass. Live model calls
are measured separately by the retained hybrid benchmarks.

The final selection contains **nine rule-based runs and two hybrid runs**
(ANTs and Lausanne). All **44/44 targeted facts** match; all **11/11 canonical**
graphs and **11/11 substrate documents** conform. Substrate validation checks
each named graph with its metadata graph (`quads=True`). This does not validate
source routing; see the remaining issue below.

| Runtime | Completed | Targeted facts | Canonical SHACL | Raw SHACL | Model calls / requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| rule-based-full | 9 | 35/35 | 9/9 | 9/9 | 0 / 0 |
| hybrid-full | 2 | 9/9 | 2/2 | 2/2 | 106 / 141 |

- **ANTs:** both runtimes keep `https://github.com/ANTsX` as owner; neither final canonical graph contains the housing foundation ROR. The final hybrid run retains the late UUID-identified Iowa organization with its ROR property and no canonical SHACL violations.
- **Citations:** all seven requested primary citations survive in both canonical and raw output. Control Toolbox was rerun after fixing HTML-link loss during context gathering.
- **ROR retention:** gimie has no strict organization-validation warnings, compared with three in the retained baseline. Producer and projection regressions exercise UUID preservation, strict identity validation and source-slice placement.
- **Linux:** GitHub still returns HTTP 403. Its five-node graph now carries an explicit unavailable-contributor warning and `context_gather: partial / contributors_unavailable`. Cache integration tests verify retry after degraded coverage and ordinary caching after recovery.

All thirteen follow-up attempts are retained. The eleven-row final selection uses
the latest attempt for each repository/runtime pair. Earlier Control Toolbox
(missing citation) and hybrid ANTs (Iowa identity violation) attempts remain
available alongside their corrected reruns. The audit is recorded in
`data/benchmarks/enrichment-fixes-20261113/regression-audit.json`.

Hybrid usage accounting remains incomplete: zero reported token totals mean
unknown usage, not zero cost. Model counts above describe the selected runs;
earlier debugging attempts also incurred calls. Infoscience rate limits, absent
Zenodo community indexing and GitHub package-scope warnings remain visible in
the retained diagnostics. The temporary sources container was removed after
verification; its index mount was read-only and extraction store writes disabled.

## Remaining membership linkage issue

The audit also found a preexisting inverse-link defect: late affiliation
resolvers create Membership nodes without adding them to the Person's
`org:hasMembership`. Raw graph routing uses that inverse link to inherit the
Person's source. These memberships can therefore land in the run metadata
graph instead of a source graph, even though raw SHACL passes.

In the matched initial eleven follow-up trials, the baseline had 32 such
memberships and the follow-up had 69. Retaining more organizations exposes more
of the existing gap; changing upstream results also affect counts. The first
hybrid ANTs attempt additionally stranded an identity-less Iowa organization,
which prompted the ROR projection regression and correction above. The original
attempt is retained. The final selection has 70 stranded Membership nodes and
no stranded Organizations. The membership linkage repair remains separate work;
neither SHACL conformance nor the targeted citation/ownership assertions certify
that routing is complete.

## Limits

These are defect regressions, not a calibrated semantic accuracy score. More
retained organizations do not prove their affiliations, and SHACL validates
structure rather than factual truth. Different live source availability and
Infoscience HTTP 429 responses prevent a controlled timing comparison. External
citation IRIs can be correct without a locally materialized Article node. GitHub
can still refuse a large repository's contributor endpoint; the fix exposes that
condition and avoids caching it as an empty success, rather than inventing data.
