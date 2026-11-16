# Twenty-repository sources benchmark

[sources-20.json](sources-20.json) contains 20 `BenchmarkCase` records and 60
positive repository-identity facts. It is an input corpus, not a completed
benchmark or a gold standard for affiliation/publication accuracy.

## Selection and verification

All 20 public repositories were verified through read-only GitHub REST requests
using `gh api repos/{owner}/{repo}` on **2026-11-13, approximately
10:41:42-10:41:44 UTC**. The GitHub connector was attempted first but returned
`Transport closed`. Each API link below is the evidence for the repository
URL, current spelling, owner account type, description, archived flag and
reported size. Descriptions are paraphrased in the rationale column.
These are live URLs, not immutable repository snapshots.

The local `origin` resolves to
[Imaging-Plaza/git-metadata-extractor](https://github.com/Imaging-Plaza/git-metadata-extractor);
that verified repository is used rather than assuming an
`sdsc-ordes/git-metadata-extractor` repository exists.

The first ten cases emphasize Swiss-named institutions, local research topics,
and projects already represented in the existing seed lists. The next five
broaden the research domains and ownership patterns; the final five are
general-purpose infrastructure or example controls. These are sampling
categories, **not verified affiliation assertions**. A GitHub organization name
alone does not establish its ROR parent, and an individual owner does not imply
any institutional affiliation.

There are 14 organization-owned and 6 user-owned repositories; 2 are archived.
GitHub's reported `size` is a repository-storage proxy in KB, not a source
line count or an extraction-cost prediction. With buckets small <1,000 KB,
medium 1,000-99,999 KB, and large >=100,000 KB, the selection has 4 small,
10 medium and 6 large repositories. The largest case (`torvalds/linux`) is
deliberately a stress case; keep fan-out bounds fixed across profiles and report
timeouts instead of silently dropping it.

| Case ID | GitHub API evidence | Owner type | Size (KB) | State | Purpose and reason |
|---|---|---|---:|---|---|
| `sdsc-ordes--gimie` | [sdsc-ordes/gimie](https://api.github.com/repos/sdsc-ordes/gimie) | Organization | 3,283 | Active | Linked metadata extraction; upstream companion to GME. |
| `imaging-plaza--git-metadata-extractor` | [Imaging-Plaza/git-metadata-extractor](https://api.github.com/repos/Imaging-Plaza/git-metadata-extractor) | Organization | 10,181 | Active | Academic/software metadata API and CLI; self-description and dependency ambiguity. |
| `epfl-lcsb--etfl` | [EPFL-LCSB/etfl](https://api.github.com/repos/EPFL-LCSB/etfl) | Organization | 16,962 | Active | Metabolic flux models with expression, thermodynamic and resource constraints; EPFL-named lab. |
| `bluebrain--snap` | [BlueBrain/snap](https://api.github.com/repos/BlueBrain/snap) | Organization | 3,018 | Archived | Neural simulation/network analysis; archived Blue Brain project. |
| `ethz-asl--kalibr` | [ethz-asl/kalibr](https://api.github.com/repos/ethz-asl/kalibr) | Organization | 3,575 | Active | Visual-inertial calibration; ETHZ-named robotics lab. |
| `ethz-adrl--control-toolbox` | [ethz-adrl/control-toolbox](https://api.github.com/repos/ethz-adrl/control-toolbox) | Organization | 14,413 | Active | Robotics and optimal/model-predictive control; a second ETHZ-named lab. |
| `swissdatasciencecenter--renku` | [SwissDataScienceCenter/renku](https://api.github.com/repos/SwissDataScienceCenter/renku) | Organization | 104,709 | Active | Reproducible collaborative data analysis platform; larger Swiss-named organization. |
| `martibosch--lausanne-heat-islands` | [martibosch/lausanne-heat-islands](https://api.github.com/repos/martibosch/lausanne-heat-islands) | User | 3,790 | Active | Lausanne urban heat simulation; individual owner and notebook project. |
| `epfl-lara--inox` | [epfl-lara/inox](https://api.github.com/repos/epfl-lara/inox) | Organization | 550,063 | Active | Higher-order functional-program solver; Scala and EPFL-named lab. |
| `lab-cosmo--pet-mad` | [lab-cosmo/pet-mad](https://api.github.com/repos/lab-cosmo/pet-mad) | Organization | 293 | Archived | Atomistic/materials modeling; small archived repository with a documented successor. |
| `astropy--astropy` | [astropy/astropy](https://api.github.com/repos/astropy/astropy) | Organization | 190,100 | Active | Astronomy library; mature research community with many contributors. |
| `scipy--scipy` | [scipy/scipy](https://api.github.com/repos/scipy/scipy) | Organization | 208,505 | Active | Scientific computing library; broad research vocabulary and many contributors. |
| `antsx--ants` | [ANTsX/ANTs](https://api.github.com/repos/ANTsX/ANTs) | Organization | 278,611 | Active | Advanced Normalization Tools; C++ scientific/imaging software. |
| `cbfinn--maml` | [cbfinn/maml](https://api.github.com/repos/cbfinn/maml) | User | 572 | Active | Meta-learning paper code; small individual-owned research repository. |
| `cheng-zhao--fcfc` | [cheng-zhao/FCFC](https://api.github.com/repos/cheng-zhao/FCFC) | User | 315 | Active | Correlation-function calculator; small C research repository and individual owner. |
| `madler--zlib` | [madler/zlib](https://api.github.com/repos/madler/zlib) | User | 5,335 | Active | General compression library; individual-owned infrastructure control. |
| `jqlang--jq` | [jqlang/jq](https://api.github.com/repos/jqlang/jq) | Organization | 8,124 | Active | JSON command-line processor; organization-owned infrastructure control. |
| `torvalds--linux` | [torvalds/linux](https://api.github.com/repos/torvalds/linux) | User | 6,479,027 | Active | Operating-system kernel; very large individual-owned infrastructure control. |
| `octocat--hello-world` | [octocat/Hello-World](https://api.github.com/repos/octocat/Hello-World) | User | 1 | Active | Minimal GitHub example; sparse-metadata control without a primary language. |
| `google--brotli` | [google/brotli](https://api.github.com/repos/google/brotli) | Organization | 42,860 | Active | Compression format; corporate organization control, distinct from academic lab handles. |

The purpose and successor of `lab-cosmo/pet-mad` were additionally checked in
its [README](https://github.com/lab-cosmo/pet-mad/blob/main/README.md)
via [GitHub's README API](https://api.github.com/repos/lab-cosmo/pet-mad/readme)
on 2026-11-13 at approximately 10:42 UTC. Retain the archived URL intentionally:
the extractor should distinguish the requested historical project from
`lab-cosmo/upet`, its documented successor.

## Expected-fact contract

Each case contains only `id`, `source_url` and `expected_facts`; each fact
contains only `subject`, `predicate` and `value`, matching
`git_metadata_extractor.experiments.benchmark.BenchmarkCase`.
Subjects are the verified canonical GitHub repository URLs, preserving the
GitHub API spelling.

Three facts are scored per repository:

- `@type = schema:SoftwareSourceCode`, the extractor's repository contract.
- `schema:name`, the GitHub API repository name.
- `pulse:repositoryHandle`, the API's bare `owner/name`.

The mapping was checked against `mapping/config/agent/repository.yaml` and
`pipeline/stages/canonical_projection.py::_project_repository`. The latter
maps the intermediate GitHub handle to the v3 bare repository handle.
No `schema:url` expectation is added: the canonical repository identity is
already the subject IRI, and the raw mapping uses `schema:url` for homepage
data. A repository homepage need not equal its GitHub URL.

Stars, forks, languages, dates, descriptions and archived flags are useful
selection evidence but intentionally excluded from expected facts because they
can change. No DOI, ORCID, ROR, author, affiliation, or inferred discipline is
asserted without an independently reviewed source.

The benchmark's existing positive-fact recall measures repository identity
retention only. It cannot detect unsupported extra articles or affiliations:
a response with all three identity facts and a false affiliation still scores
100% here. Inspect extracted organizations, memberships, articles, evidence
citations and warnings separately, especially for the five controls. Controls
mean no presumed Swiss/research affiliation; they do **not** assert that a
project has no papers, researchers or research users.

## Source coverage and interpretation

Selection accounts for the operational inventory supplied with this task:
52 Qdrant collections; worldwide ROR 125,065 records, Swiss ROR 1,854;
GitHub 1 record, Zenodo 4, and many research collections empty. This note
records that supplied inventory, not an independent recount performed while
building this corpus. Re-measure it with the benchmark run because index
contents can change.

Institutional names may exercise the populated ROR index, while broad research
and infrastructure controls test behavior beyond the sparse indexed content.
An empty retrieval result is a coverage observation, not proof that an
affiliation or publication does not exist. A failed service request is a
different outcome from a successful query returning zero records; preserve that
distinction in results. Public GitHub existence does not imply local indexing.

## Candidate five-case hybrid subset

Use these existing case IDs for an initial bounded hybrid comparison after the
20-case rule-based baseline; this file does not run or authorize model calls.

1. `sdsc-ordes--gimie`: small companion project and a local metadata baseline.
2. `epfl-lcsb--etfl`: institution-named life-science project.
3. `ethz-asl--kalibr`: institution-named C++ robotics project.
4. `martibosch--lausanne-heat-islands`: individual-owned notebook research.
5. `octocat--hello-world`: sparse-metadata negative control.

Keep the same source URLs, case IDs, input facts, fan-out limits, sources
inventory and cache policy across runtime comparisons. Preserve every trial,
including failures and empty retrievals, and report model usage separately from
identity recall. The subset deliberately excludes the largest stress cases
from the initial paid comparison.
