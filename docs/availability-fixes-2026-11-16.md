# Membership routing and source availability

This local follow-up repairs the inverse membership links and addresses the
three availability findings from the [November 13 enrichment review](enrichment-fixes-2026-11-13.md).
Historical benchmark outputs are preserved. These checks do not replace a new
20-repository rule-based/hybrid benchmark.

## Memberships survive validation and reach the source graph

Company ROR, company GitHub-handle, deterministic bio and LLM bio producers now
link accepted Membership IDs from the Person's `org:hasMembership`. They register
the same body in `reconciled.memberships`, which strict validation reads, and
the entity bucket used by late producers. Existing reconciled memberships keep
their UUID, source evidence, dates and role; reruns do not recreate them.

Bio stages discover existing affiliations in either collection. Missing people
cannot create orphan memberships, and malformed ROR patches remain rejected.
The regression tests execute producer → strict validation → assembly → substrate
projection and assert source placement, including the absence of Membership
entities from the metadata graph. SHACL alone cannot detect that routing error.

The previously reported 70 stranded memberships remain in the historical
November 13 artifacts. The repair applies to new extractions; it does not rewrite
stored graphs or claim that the entire corpus has been regenerated.

## Linux contributor recovery has explicitly partial coverage

The live diagnostic found a valid GitHub token with approximately 4,800 core
requests remaining. Linux's contributors endpoint returned a repository-size
refusal, while the same token succeeded for gimie. Authentication and quota
were not the cause.

Only that explicit oversized-history 403/422 response permits one request to
`GET /repos/{owner}/{repo}/commits?per_page=100&page=1`. Generic failures,
Retry-After responses and exhausted quota do not trigger it. The provider uses
linked User authors, deduplicates their logins and ignores committers, anonymous
authors and malformed identities. The upstream distinction between author and
committer is documented in [GitHub's commits API](https://docs.github.com/en/rest/commits/commits).

The live provider probe recovered **26 linked authors** for Linux. Its coverage
was `partial`, source `github_rest_commits`, scope `recent_commit_authors`, reason
`recent_commit_sample`. Every sampled contribution count was unknown. This is a
recent sample, not all contributors or a ranking of the largest contributors.
Separate commit-bookend queries may supply independently verified counts.
Partial/unavailable results remain excluded from the pipeline cache.

## Infoscience failures remain visible and recoverable

The HTTP helper now serializes requests across the provider's thread/async
bridge. Queueing, network requests and retries share a 30-second budget, with
at most three transient attempts. Numeric and HTTP-date Retry-After values are
respected; deadlines beyond the current budget become a shared cooldown rather
than an early retry. Cancellation releases the gate. This is process-local:
separate service workers still have separate limits.

Availability failures propagate as provider errors, preventing fallback search
storms and cached empty successes. Person and organization enrichment keep valid
GitHub/ROR entities with warnings. Person, organization and article agents mark
unavailable providers explicitly, so their incomplete response cannot enter the
pipeline cache. Existing provider and pipeline cache keys were versioned to
bypass old entries without deleting other cached data. Confirmed 404 UUID
lookups retain their `/core/items` fallback and return `None` when absent.

A bounded live search from Docker answered successfully in **0.77 seconds**. Tests exercise
throttling, deadlines, cross-thread serialization, cancellation and recovery;
one successful live request does not establish sustained upstream availability.

## Zenodo community index restored in sources

The missing local database was built through the sources CLI into a staging
directory, checked and atomically published at:

```text
data/index/zenodo_communities/duckdb/zenodo_communities.duckdb
```

It contains **508 distinct communities**, including all **34 curated slugs**:
EPFL 237, ETH Zurich 250, CERN 20, CERN openlab 1. The database is metadata-only;
it requires neither embeddings nor Qdrant. Discovery is capped at five pages
of 50 hits per query, and seven of eight configured queries exceeded that cap.
These counts describe the configured catalog, not exhaustive Zenodo coverage.

A temporary sources container returned real records for `epfl`, `epfl-chili`,
`sae-eth`, `cerneducation` and `cernopenlab` through authenticated HTTP. An MCP
lookup also returned `epfl-chili`; unauthenticated HTTP returned 401. The server
reported `0.2.0.dev2`. The source and client containers used an internal Docker
network; the index mount was read-only. Both containers and the temporary
network were removed after verification. GME did not open the database or import
index code. Earlier native probes also exposed stale editable-install metadata,
which was corrected locally; final checks use the container builds.

The source checkout's bootstrap documentation was committed independently as
`0de3862ba8c73a4730b70339ba1ebcc414cc0962`; GME's pairing lock now records it.
The build itself used its code-identical predecessor `a812bb2`, retained in the
bootstrap report. Both commits remain local; remote CI cannot fetch unpublished
source commits until publication is explicitly requested.

## Verification and artifacts

Final validation ran in Linux Docker containers. The full V2 suite passed
**2,084 tests**, with **2 skipped** and **3 credential-dependent tests
deselected**. Supporting developer/docs/utility checks passed **23 tests and
18 subtests**. The independent sources checks passed **67 tests**, and the
paired HTTP contracts passed **2 tests**. Mapping validation passed for
**23 mappings and 3 policies**, and strict MkDocs build passed. Dependency
deprecation warnings remain; new tests pass Ruff.

The initial native suite overlapped the last cache fix and loaded its earlier
code, yielding four cache-regression failures. Its result is superseded by the
complete Docker run of the final code, not reported as a passing run.

`tools/image/Dockerfile.test` installs from each project's lock with `--locked`;
its allowlisted build context excludes credentials, host environments and index
data. The stale GME lock was refreshed to remove the pre-split sources/storage
dependencies without upgrading retained packages. The standalone GME image is
checked to contain no `open_pulse_sources` package. Only the separate contract
image combines both projects for paired-service tests.

```powershell
just test-docker
# Equivalent, without just:
docker build -f tools/image/Dockerfile.test -t gme-test:local .
docker run --rm --network none gme-test:local
docker run --rm --network none --entrypoint python gme-test:local -m git_metadata_extractor.mapping --check
docker run --rm --network none --entrypoint python gme-test:local -m mkdocs build --strict

# Same harness, independent sources dependency graph:
docker build -f tools/image/Dockerfile.test --build-arg "TEST_EXTRAS=--extra dev" -t gme-sources-test:local dev/open-pulse-sources
docker run --rm --network none gme-sources-test:local tests/service/test_indices_freshness_and_zenodo_communities_search.py tests/service/test_extractor_operations.py tests/service/test_api_manifest.py tests/index/zenodo_communities -q
```

Local evidence is retained under `.staging/availability-20261116/`:
`github-probe.json`, `docker-provider-probe.json`, `docker-source-probe.json`,
`docker-v2.log`, other verification logs/exit records and bounded probe scripts.
The Zenodo build report, checksum, query
limits and counts are in `data/zenodo-bootstrap-20261116-report.json`; staging
data remains under `data/index-bootstrap/zenodo-20261116/`.

No full LLM run, new hybrid corpus, production image deployment or store migration
was performed for this follow-up. Test images remain local; deploying the fixes
and rebuilding existing stored graphs remain separate steps.
