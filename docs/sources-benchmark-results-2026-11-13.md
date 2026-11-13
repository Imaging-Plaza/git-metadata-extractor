# Sources benchmark: 20 repositories, two runtimes

This report records the live run on **2026-11-13**: the same 20 repositories in `rule-based-full` and `hybrid-full`, one retained trial per pair. The useful outcome is a structurally valid repository graph with real indexed ROR activity, but publication recall and affiliation retention remain incomplete, and ANTs has a strongly suspect institutional ownership match. This is an operational comparison with human-reviewed examples, not a semantic-accuracy leaderboard.

## Execution and structural checks

| Runtime | Completed / retained | Repository identity facts | Canonical SHACL passed | Observed model calls | Observed model requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| rule-based-full | 20 / 20 | 60 / 60 | 20 | 0 | 0 |
| hybrid-full | 18 / 20 | 54 / 54 | 18 | 780 | 1132 |

Of 120 planned repository-identity assertions, 114 were observed matching and 6 were unmeasured. An unmeasured assertion is not counted as a pass.

Failed or timed-out trials remain explicit outcomes, not successful empty graphs. Identity/SHACL counts apply only where validation metrics were retained; unmeasured outcomes are N/A. Identity facts check repository type, name and bare GitHub handle only. They do not score the truth of affiliations, extra articles, missing contributors or omitted citations. Final SHACL success also does not measure completeness: earlier stages can drop invalid entities and prune their references.

Runs used live providers, cold per-trial provider directories, retained run reports and substrate output. Sources mounted existing indexes read-only; the extractor accessed them through HTTP. The first sequential baseline case was retained; an unfinished second attempt was interrupted for scheduling and rerun. Remaining baseline cases used three isolated workers. Hybrid began with five sequential cases, then expanded to the other fifteen with two workers when the requested matrix grew to both runtimes for all 20. Shared providers, changing upstream availability and overlapping execution prevent controlled speed or causal accuracy comparisons. Per-trial durations remain in the raw artifacts. Manifest controls were `max_concurrent_agents=2` for rule-based and `4` for hybrid, a 600-second trial deadline, and `V2_LLM_TIMEOUT_SECONDS=120`. Canonical and substrate output were enabled, with `read_only=true`. The configured model was `openai/gpt-oss-120b` at `https://inference-rcp.epfl.ch/v1`; rule-based execution made no model calls. Different generator concurrency is an additional performance-comparison confounder.

Hybrid token accounting is incomplete. Recorded zero token totals are unknown usage, not free execution, and no monetary cost is inferred. Counts of model activities and model requests are distinct measurements and retained subtotals. Work performed before an unreported timeout may be absent from these counts.

## Paired graph results

RB = rule-based; H = hybrid. Entity totals include profiles, memberships, deposits and contributions. More nodes do not establish better accuracy. Article counts concern `schema:ScholarlyArticle`, not every DOI mentioned in a README.

| Repository | RB entities | H entities | RB articles | H articles | H model calls |
| --- | ---: | ---: | ---: | ---: | ---: |
| sdsc-ordes/gimie | 87 | 87 | 0 | 0 | 48 |
| Imaging-Plaza/git-metadata-extractor | 38 | 40 | 0 | 0 | 22 |
| EPFL-LCSB/etfl | 30 | 30 | 3 | 3 | 22 |
| BlueBrain/snap | 63 | 63 | 0 | 0 | 27 |
| ethz-asl/kalibr | 110 | 114 | 0 | 0 | 82 |
| ethz-adrl/control-toolbox | 50 | 57 | 0 | 0 | 39 |
| SwissDataScienceCenter/renku | 90 | 125 | 0 | 0 | 62 |
| martibosch/lausanne-heat-islands | 6 | 6 | 0 | 0 | 5 |
| epfl-lara/inox | 126 | 143 | 0 | 0 | 71 |
| lab-cosmo/pet-mad | 100 | 66 | 5 | 0 | 54 |
| astropy/astropy | 138 | failed | 0 | N/A | N/A |
| scipy/scipy | 110 | failed | 0 | N/A | N/A |
| ANTsX/ANTs | 148 | 171 | 0 | 0 | 109 |
| cbfinn/maml | 23 | 23 | 0 | 0 | 14 |
| cheng-zhao/FCFC | 10 | 13 | 0 | 1 | 9 |
| madler/zlib | 94 | 98 | 0 | 0 | 64 |
| jqlang/jq | 98 | 102 | 0 | 0 | 68 |
| torvalds/linux | 5 | 5 | 0 | 0 | 6 |
| octocat/Hello-World | 10 | 10 | 0 | 0 | 10 |
| google/brotli | 94 | 102 | 0 | 0 | 68 |

### Trials without complete graphs

| Repository / runtime | Recorded error | Observed seconds |
| --- | --- | ---: |
| astropy/astropy / hybrid-full | TimeoutError | 600.0 |
| scipy/scipy / hybrid-full | TimeoutError | 600.0 |

These outcomes have N/A graph, identity and SHACL measurements. Work before the timeout can incur unmeasured model usage; absence of a result is not evidence of zero calls or cost.

## What actually used the indexed sources service

This was a mixed-provider pipeline. GitHub/GIMIE supplied repository context, while live ORCID and Infoscience providers also executed. The table below counts remote adapter/service events in retained results, not configured providers or unique returned records. Failed trials without retained reports have unknown activity. A source-record URL event indicates that the returned evidence contained a URL; it does not prove a final graph assertion.

| Observed event / status | Rule-based | Hybrid |
| --- | ---: | ---: |
| `epfl_graph_rag.search` / adapter_returned | 0 | 18 |
| `federated_rag.search` / adapter_returned | 0 | 90 |
| `infoscience_rag.search` / adapter_returned | 0 | 14 |
| `ror_rag.search` / adapter_returned | 247 | 363 |
| `sources.operation` / failed | 0 | 90 |
| `sources.operation` / success | 0 | 5 |

Events carrying source-record URLs: **247 rule-based**, **453 hybrid**.

`adapter_returned` is deliberately weaker than verified HTTP success. Existing remote adapters return empty values for both genuine zero-hit results and some service failures; observation wrappers can label both as success. Failed `sources.operation` events are explicitly reported. A successful empty result, a service error and an unavailable index cannot always be distinguished from retained reports alone.

Aggregate sources HTTP logs provide an additional transport check: 958 responses including preflight, with 684 ROR searches, 105 federated searches, 41 EPFL Graph searches and 14 Infoscience searches returning HTTP 200; five Infoscience acronym operations also returned 200, while 105 Zenodo community operations returned 503. The remaining four responses were health/manifest/preflight checks, including one incorrect initial path. These totals include activity from unretained timeout work and preflight, have no per-trial attribution, and must not be added to the run-report counts above. HTTP 200 does not establish nonempty results or factual correctness.

A specific indexed-operation coverage failure was diagnosed: Zenodo community lookup returned HTTP 503 because `data/index/zenodo_communities` contained no files. A read-only diagnostic inside the sources container reached `_read_connection` and raised `FileNotFoundError("Source index is unavailable")`. This is a missing local community index, not evidence of read-only mount permissions or an arbitrary upstream outage. Other missing/failed indexes were not blanket-diagnosed.

ETFL baseline publication retrieval was recorded as `infoscience.search_publications`, with no `infoscience_rag` event: those articles came through the live provider path, not demonstrated local Infoscience indexing. SourceSnapshot nodes for Infoscience, ORCID and ROR describe source-service version metadata; their presence is not proof that each local index supplied records.

The recorded inventory had 52 Qdrant collections with uneven coverage: ROR worldwide 125,065 points, ROR Switzerland 1,854, one GitHub repository point, four Zenodo records, populated ORCID/OA Monitor/SNSF collections, and several empty research collections. Counts and deployment versions are observations, not an immutable dataset snapshot. Sources reported `0.2.0.dev2` at lock revision `a812bb2c5c84a5f5357cb265f50d65e85662c4d2`; GME started from `742d076a09d6563f21b40fc4444b5427e56c6aab` with local benchmark/report changes. Exact image IDs and inventory are retained locally.

## Does the output make sense?

**Strongly suspect ANTs ownership in both runtimes.** The graph asserts `ANTsX org:unitOf https://ror.org/03zh00e46` and that the ANTs repository is owned by this ROR organization, "Normalization Housing Foundation". Retained GitHub metadata describes "Advanced Normalization Tools Ecosystem", biomedical image-analysis software, website `ants.dev`, and US locations. The retained ROR candidate is a nonprofit in Japan with website `normalize.or.jp`. No retained evidence bridges these identities; a shared word, "Normalization", is insufficient. This is an unsupported and strongly suspect parent/ownership match, not merely a contributor career affiliation. It warrants correction review, but this benchmark does not claim a calibrated false-positive rate.

**Useful positive checks.** ETFL retains the main ETFL paper, DOI `10.1038/s41467-019-13818-7`, whose title and DOI are explicit in the saved README. Baseline pet-mad preserves the requested archived repository instead of replacing it with its announced successor `upet`; two of its five article titles exactly match the README's requested model citations. The wide international organization list for gimie is largely dated ORCID career history for contributors, not a claim that all those institutions own the repository. General-purpose controls return no scholarly articles in baseline; their contributor affiliations are not automatically false repository affiliations.

**Confirmed citation omissions.** Seven manually reviewed baseline examples contain explicit project citation evidence already fetched during extraction, but return no articles:

| Repository | Retained project evidence |
| --- | --- |
| ethz-adrl/control-toolbox | README "How to cite the CT": Control Toolbox paper, SIMPAR 2018; arXiv `1801.04290`. |
| martibosch/lausanne-heat-islands | README Citation: InVEST urban heat mitigation paper, DOI `10.5194/gmd-14-3521-2021`. |
| scipy/scipy | `CITATION.bib`: SciPy 1.0, DOI `10.1038/s41592-019-0686-2`. |
| astropy/astropy | `CITATION.cff` preferred article: 2022 Astropy Project paper, DOI `10.3847/1538-4357/ac7c74`. |
| cbfinn/maml | README explicitly says the code accompanies the ICML 2017 MAML paper; arXiv `1703.03400`. |
| ANTsX/ANTs | `CITATION.bib`: ANTsX ecosystem paper, DOI `10.1038/s41598-021-87564-6`. |
| cheng-zhao/FCFC | README requests the FCFC paper, DOI `10.1051/0004-6361/202346015`; present only in the hybrid output. |

In all seven baseline cases, the root repository also has no `schema:citation` value, so the missing article is not merely retained as an external citation IRI. These are demonstrated output gaps, not an exhaustive recall denominator or proof of a single algorithmic cause. A Zenodo software/archive DOI is not automatically a scholarly article. Likewise, extra ETFL and pet-mad articles with plausible topics but no explicit README match remain review candidates; this review does not declare them false.

**Hybrid differences are confounded by live retrieval.** Kalibr hybrid restores Nvidia and Hexagon organization nodes plus two memberships relative to baseline (114 versus 110 entities), and changes root discipline. This is a verified structural restoration; the membership truth and discipline accuracy were not independently established. Pet-mad hybrid instead returns 66 entities and no articles versus baseline 100 and five articles. Its article generation returned no publications; refinement and article validation removed none. Existing worker logs confirm HTTP 429 on both hybrid publication queries. This is not evidence that hybrid reasoning deleted the papers or is intrinsically worse. FCFC shows the opposite observed difference: hybrid retains the README-requested paper, DOI `10.1051/0004-6361/202346015`, while baseline has none. Its article appears during generation before refinement, so the gain cannot be assigned to the LLM refiner. Execution monitoring found Infoscience HTTP 429 in 23 captured log files (some cover batches); changing live retrieval availability materially confounds these comparisons.

**Linux is a degraded extraction, not a successful scale benchmark.** Both runtimes return five nodes and no contributions. Existing GIMIE sidecar logs report that GitHub rejected the history/contributor request as too large and GIMIE TTL extraction failed. `RealGitHubProvider.get_contributors` in `git_metadata_extractor/providers/github_provider.py:1808` depends on contributor logins in GIMIE data and returns an empty list without a direct contributor fallback. REST metadata and the owner stub kept the pipeline alive. The roughly 59-second completed baseline trial and passing identity/SHACL checks do not establish full Linux coverage.

## Reproducible affiliation-retention defect

The strict intermediate contract rejects some organizations even when their ROR identity has been resolved. In `git_metadata_extractor/schema/json/strict/organization.schema.json:141`, `anyOf` requires top-level `schema:identifier`, `pulse:githubOrganizationHandle`, or `pulse:infoscienceOrganizationIdentifier`; `id` and nested `identifiers.pulse:ror` do not satisfy it. `git_metadata_extractor/validation/schema_validation.py::StrictSchemaValidator.validate` validates that payload, and `git_metadata_extractor/pipeline/run.py::_strict_validation` feeds invalid entities to exclusion and subsequent reference pruning.

A sanitized retained example is Georgia Tech Research Institute (`https://ror.org/04qfrh333`), rejected at `<root>` with constraint `anyOf` despite its ROR ID and nested ROR identifier. An offline reproduction with a valid synthetic UUID and `idSource=pulse:ror` fails only `anyOf`; adding top-level `schema:identifier` equal to the ROR URL passes. This demonstrates a producer/strict-contract mismatch, not validator misbehavior. Some other excluded organizations also lack UUIDs. Some candidates may be false matches, so preserving every rejected organization would not be a sound fix.

## Follow-up priorities

1. Require corroborating identity evidence for ROR parent/ownership matches; a shared generic name token must not be enough. Prioritize the unsupported ANTs/Normalization Housing Foundation relation.
2. Align ROR-backed organization production with the intended strict identity contract, then test retained organizations and memberships, not just final conformance.
3. Feed already-fetched README/CITATION evidence into conservative article creation or retrieval; validate requested project citations separately from incidental references.
4. Surface degraded GIMIE/contributor coverage and upstream rate limits explicitly; distinguish HTTP failure, successful empty retrieval and unavailable indexes.
5. Compare runtimes against the same retained provider responses before drawing causal quality or speed conclusions, and repair token-usage accounting before cost comparisons.
6. Build a small reviewed gold set for affiliation precision, recommended-paper recall and discipline quality. The current three identity facts per repository cannot provide those metrics.

## Reproduction and retained artifacts

Corpus: `config/benchmarks/sources-20.json`; rationale: `config/benchmarks/sources-20-notes.md`. Raw results, trial metrics, provider caches, deployment inventory and sanitized upstream evidence remain under ignored `data/benchmarks/sources-20261113/`. This report does not expose raw person records or provider headers.

The aggregate `matrix-summary.json` checks expected pairs, missing trials and duplicates. `quality.json` / `quality.md` provide offline structural triage and runtime comparisons; `findings.md` records incremental manual review; `readme-doi-review.json` is a candidate scan, not an automatic relevance judgment. `linux-sidecar-evidence.json` and `upstream-status-evidence.json` retain sanitized failure evidence; `sources-http-statuses.json` records aggregate transport responses including preflight.

Regenerate offline triage with:

```bash
python scripts/v2/review_benchmark.py data/benchmarks/sources-20261113
```

The reviewer makes no live model/provider calls and never treats skipped SHACL as a pass. Its `acceptable`, `needs_review` and `failed` labels describe implemented checks, not automated factual truth.
