# Behaviour-freeze baseline — findings

Phase 0 of the ontology-first refactor. This is the measured "before" picture the
later phases are checked against.

**Recorded:** 2026-09-08 · `develop` @ `0757f4a` + Phase 1 deletions + the
Selenium fix + the Phase 3 stage protocol + the three handle fixes ·
`agent_runtime=rule_based` · pipeline cache **off** · 120 URLs · 0/119
cache-served, verified.

The first version of this document described a *pre-fix* state. Those numbers
are kept below as the "before" column because they are what motivated the
fixes — but `baseline.signature.json` now records the **post-fix** graph, so
later phases diff against corrected behaviour.

## Run conditions — the pipeline cache MUST be off, and the provider cache MUST match

```
V2_PIPELINE_CACHE_ENABLED=false
V2_PROVIDER_CACHE_PATH=.cache/v2/providers.db   # the default; what this baseline used
```

**This is not optional and it is not obvious.** A pipeline-cache hit returns the
*whole* stored response, so `stats.duration_ms` comes back as the original run's
20-plus seconds and `stages_completed` lists every stage. A replayed result is
indistinguishable from a computed one inside the file. Only the job envelope
gives it away: `completed_at - started_at` is ~1 ms against a reported 22 s.

The first attempt at this baseline was recorded with the cache on, and 76 of 119
results were replays of an earlier code state. `corpus_signature.py` now refuses
to record or diff a majority-cached directory (`_cache_served_fraction`), so the
mistake cannot repeat silently. Deleting the result files does **not** clear the
cache — the flag is the only reliable control.

**The provider cache is the second, subtler trap.** `AGENTS.md` recommends a
separate cache path per run profile, which is right for isolating runs from
each other and wrong for comparing one against this baseline: a fresh provider
cache makes every external lookup live, and the world moves. Measured
2026-09-09 with `.cache/v2-rule-based/providers.db` against a baseline recorded
on the default path — **13 of 120 changed**, all of it drift:

| Repos | Field | Why |
|---|---|---|
| 9 | `pulse:repositoryForks` | GitHub fork counts moved by one or two overnight |
| 4 | `pulse:ror` + `schema:name` | the ROR fuzzy search returned a different top hit (`Ness Technologies` → `Cirrascale (United States)`, and three like it), propagating into `org:unitOf` / `pulse:ownedBy` / `pulse:owns` |

Re-run on the baseline's cache path: 120 identical. The tell for this failure
mode, as opposed to a real regression, is that a *subset* of repos changes and
only in fields that come straight from a live API — no structure, no stage
output.

Recorded also without Qdrant and without a Selenium grid, both deliberately:

- **Qdrant absent** — `data/index` is empty, so a running Qdrant would have no
  collections and behave identically. `rule_based_disciplines` fails open
  (90 warnings) and discipline tagging is degraded throughout. A later run
  *with* hydrated indices will legitimately differ; that is not a regression.
- **`SELENIUM_REMOTE_URL` unset** — ORCID activity scraping is skipped. Before
  the fix in `providers/github_accounts/users_parser.py` this silently launched
  a browser on the operator's desktop.

Reproduce with:

```
docker run -d --name gme-gimie-api -p 15400:15400 gme-gimie-api:0.7.2
GIMIE_API_URL=http://localhost:15400 V2_PIPELINE_CACHE_ENABLED=false just serve-dev
PARALLELISM=4 scripts/v2/batch_extract.sh rule_based \
    tests/v2/corpus/seeds.txt data/corpus/baseline
python scripts/v2/corpus_signature.py record data/corpus/baseline \
    -o tests/v2/corpus/baseline.signature.json
```

Two things `just` supplies that a bare invocation does not, and both fail
opaquely. `set dotenv-load := true` puts `API_TOKEN` in the environment —
without it `batch_extract.sh` reports `FAIL submit` for all 120 repos and only
the per-repo log under `_logs/` mentions the 401. And `jq` has to be on `PATH`;
it is not in this host's default profile, though it is in the flake devShell.

## What the corpus contains

| | |
|---|---|
| URLs | 120 (90 repositories, 30 owners) |
| Completed | 119 |
| Failed | 1 — `cosmo-epfl` returns 404 from GitHub; the org no longer exists. A real upstream state, correctly frozen as an expected error. |
| Entities | 434 |

Entity ids by namespace — this is the migration blast radius, since under v3 every
Person without an ORCID and every Organization without a ROR is re-identified:

| Namespace | Count |
|---|---|
| `github.com` | 192 |
| `urn:pulse:repo-author:` (synthetic placeholders) | 84 |
| `ror.org` | 64 |
| `orcid.org` | 47 |
| `doi.org` | 26 |
| `urn:pulse: (UUID-keyed)` | 21 |

## Signature stability is verified, not assumed

21 of 434 entities are UUID-keyed, so their `@id` changes every run. The signature
replaces those with a hash of the entity's own content. Verified by re-extracting
`HugoLeRoy94/Module_cpp` with `V2_PIPELINE_CACHE_ENABLED=false`: one of seven UUIDs
regenerated, and the resulting signature was byte-identical in both entities and
edges. Without this the differ would report noise on every run.

## What the corpus found, and what fixing it did

Three defects, all one confusion: `pulse:githubUsername` and
`pulse:githubOrganizationHandle` are **URL-valued** in the strict schemas
(`^https://github\.com/...`), and the code treated them as bare handles in some
places and URLs in others — in both directions.

| # | Site | Defect |
|---|---|---|
| 1 | `_extract_owner_from_repo_handle` | Expected bare, field held a URL, so `split("/")[0]` returned `'https:'` |
| 2 | `demote_github_props_to_units` | Concatenated `https://github.com/` onto a value that already was one |
| 3 | `_synthesize_owner_person_stub` | Wrote a bare handle where the schema demands the URL, so the stub failed its own validator |

Measured across the 120-URL corpus, cache off both times:

| Metric | Before | After |
|---|---|---|
| Corrupt `github.com/https:` values | 464 | **0** |
| Entities in output | 346 | **434** |
| Total warnings | 1,442 | **1,145** |
| SHACL violations | 60 | **10** |
| — `schema:author` `sh:minCount 1` | 46 | **0** |
| Runs carrying ≥1 violation | 47 of 120 (39%) | **5 of 120 (4%)** |

Entity count *rising* is the confirmation that matters: the stubs now survive
strict validation instead of being excluded, so `schema:author` gets populated
rather than emptied.

**But be clear about what those 88 entities are.** 84 of them are
`urn:pulse:repo-author:<org>/<org>` — synthetic Person placeholders minted
because the repository's owner is an *Organization* while SHACL requires
`schema:author` to target a `schema:Person`. They carry no information beyond
the org's own handle.

So this fix made an existing workaround work correctly; it did not make the
model right. Under v3's profile model the workaround becomes unnecessary — an
`org:Organization` owns repositories directly through `pulse:owns`, and
`schema:author` points at real people or is absent. **Expect these 84
placeholders to disappear in phase 6, not to be preserved.** Anyone reading a
future diff should treat their removal as progress rather than regression.

### The cascade, for the record

Defect 3 was the expensive one. `guarantee_repo_author` salvages a repository
whose `schema:author` came out empty by synthesising an owner Person. The stub
set `pulse:githubUsername` to the bare handle, which fails every branch of the
schema's `anyOf`, so:

1. strict validation **excluded the stub**
2. the repository's `schema:author` reference was orphaned
3. `prune_dangling_refs` dropped the associated Contribution
4. `schema:author` stayed `[]` and SHACL reported `sh:minCount 1`

The salvage ran 46 times and fixed nothing, because it rebuilt the entity from
a value the schema rejects. Seven warning kinds fired at exactly 46 as a result.

### Two tests were defending the bugs

`test_synthesize_owner_person_stub_is_a_valid_person_shape` — a test *named* for
schema validity — never called a validator. It hand-asserted field values and
**hardcoded the invalid bare handle**.
`test_demote_synthesizes_unit_when_parent_handle_has_no_matching_unit` did the
same for the organization handle. 1,500 tests passed while 39% of extractions
shipped a SHACL violation.

Both now assert the correct form *and* run `StrictSchemaValidator`. New tests:
`test_synthesize_owner_person_stub_passes_strict_validation`,
`test_synthesized_org_unit_passes_strict_validation` (both handle forms), and
`tests/v2/test_owner_handle_from_repo_handle.py`.

## The residual 10 — not a bug; v3 drift

| Count | Path | Constraint |
|---|---|---|
| 5 | `pulse:githubUsername` | `sh:pattern` mismatch |
| 5 | `pulse:githubRepositoryHandle` | `sh:pattern` mismatch |

**Correcting an earlier conclusion here.** This section first called these a
producer-side bug — the same URL/bare confusion a fourth time, to be fixed. That
is wrong, and generating models from the v3 shapes is what showed it.

All ten are *bare* values failing v2.1.2's *URL* patterns:

```
focus=https://github.com/HippolyteKarakostas
path=pulse:githubUsername
message=Value does not match pattern '^https://github\.com/[A-Za-z0-9]...'
```

v3's canonical shapes require the **bare** form for `pulse:orcidIdentifier`,
`pulse:doi` and `pulse:repositoryHandle`. So these producers are already
v3-shaped, and "fixing" them to the URL form would be work undone at migration
time.

Leave them. The v3 migration resolves them — and it is itself a **value**
migration nobody had scoped: every ORCID and DOI in the store moves from
`https://orcid.org/0000-...` to `0000-...`. See §2.4 of
`ONTOLOGY_V3_REQUIREMENTS.md`.

## Run conditions carried over

`rule_based_disciplines` still fails open 90 times because Qdrant is absent and
`data/index` is empty; discipline tagging is degraded throughout. A later run
with hydrated indices will legitimately differ.

## How to use this

```
# after any refactor phase, re-run and compare. The server MUST have
# V2_PIPELINE_CACHE_ENABLED=false and the baseline's V2_PROVIDER_CACHE_PATH.
scripts/v2/batch_extract.sh rule_based tests/v2/corpus/seeds.txt data/corpus/after
python scripts/v2/corpus_signature.py diff \
    tests/v2/corpus/baseline.signature.json data/corpus/after -v
```

Exit code is non-zero if anything changed. Entity and edge sets are compared; key
order, UUIDs, timings and warning wording are not.

Phases 2 and 3 should produce an empty diff — they are mechanical. Phases 4 onward
will not, and the diff is meant to be reviewed entity by entity rather than approved
in aggregate.

**Phase 3 result:** verified empty. `before-clean` (HEAD) and `after-nocache`
(Phase 3 stage protocol) were both collected with the cache off, 0/119
cache-served each, and compared 120 identical / 0 changed — the stage-protocol
move changed nothing, as a mechanical refactor should not.

The handle fixes that followed *did* change output, deliberately, and this
baseline was re-recorded from that corrected state.


## Re-baselined 2026-09-08 (Phase 2 + Phase 3 completion)

Two mechanical moves and one deliberate fix, verified separately in one run:

```
identical : 115        cache-served : 0/119 (median wall/reported 1.31)
changed   :   5        server-logged pipeline cache hits: 0
```

The 5 changed results are the intended fix and nothing else — each diff shows
only `pulse:githubUsername` going bare → canonical URL, with `schema:name`
correctly left bare. Every stage moved into `pipeline/run.py` in this session
(11 more, reaching 24 of 33) contributed **zero** diff, which is what a
mechanical move must do.

`pulse:githubUsername` bare values: 5 → **0**. SHACL violations: 10 → **5**.

The cause was the third of three Person-stub producers in
`ownership_check.py` — the fork-parent emitter, which writes an owner stub for
a fork's upstream owner. It was found only because Phase 2 deleted a stale
duplicate of the JSON Schemas: the copy the test suite validated against had
no `pattern` on `pulse:githubUsername`, so nothing failed. The SHACL gate had
been reporting it as a warning all along.

Baseline re-recorded from this corrected state.


## Phase 3 sequencing complete — verified 2026-09-08

All 33 post-agent stages now run from ordered lists in `pipeline/run.py`
(7 chains, 34 entries). Corpus:

```
identical : 120        cache-served : 0/119 (median wall/reported 1.16)
changed   :   0        server-logged pipeline cache hits: 0
```

An empty diff alone would not prove the stages ran, so stage execution was read
from the server log: `strict_validation`, `output_assembly`, `link_veracity`,
`jsonld_build`, `shacl_gate`, `permissive_validation` and `reconciliation` each
logged 119 times, and `rule_based_disciplines` exactly **90** — matching the 90
repository seeds among the 120 (the remaining 30 are user/org roots). A gate
that fired 90/120 on the right subset is evidence it discriminates; one that
fired 0 or 120 would not have been.

SHACL violations steady at 5, bare `pulse:githubUsername` values at 0.


## Substrate writer verified — 2026-09-09

The substrate layer (`PROVENANCE_ARCHITECTURE.md` phase 3) added two stages to
`PAYLOAD_CHAIN` and a `substrate` field to the response. Both are additive, so
`output` had to be untouched — verified with the writer **on**, which is the
stronger direction:

```
identical : 120        substrate_projection : 119 server-logged
changed   :   0        substrate_write      : 119 server-logged, 0 failures
```

Read back out of the live Oxigraph the run wrote to:

```
named graphs            : 304        triples : 4,867
by slice                : github 119 · ror 41 · infoscience 25 · meta 119
distinct ExtractionRuns : 119
dangling run references : 0
entities in a meta graph: none
```

Those slice counts match the *offline* projection of `after-canon` exactly, so
the pipeline path and the measurement path agree rather than sharing a bug.

Re-extracting one URL the store already held took 304 → 307 graphs and left
that repository anchored in two runs' slices — append-only, as the IRI scheme
intends, with nothing overwritten.

The first attempt at this verification reported 13 changed — see the provider
cache note in "Run conditions" above. None of it was the refactor.


## Unification verified — 2026-09-09

Phase 4 reads the substrate the corpus run wrote and produces
`urn:pulse:graph:canonical`. Two numbers matter and neither is the corpus diff:

```
unification : 624 records / 185 graphs -> 587 clusters, 16 cross-run, 0 stale
canonical   : 3,094 triples, 0 SHACL violations, idempotent over 3 passes
```

**The corpus cannot validate the value selector.** Of the 17 entities that
appear in more than one run, twelve properties differ — nine are
`pulse:partOfRun` (per-run by design) and three are set accumulation. There is
not one genuine value conflict in 119 runs, so `SELECT` is fixture-tested. The
same blind spot as the RAG providers, and worth remembering before reading a
green unifier run as coverage.

What the corpus *does* validate is accumulation, and it does so concretely:
`https://ror.org/02s376052` (EPFL) ends up with 10 `org:hasUnit` edges in
`graph:canonical`, one from each of ten runs. No single `/v2/extract` can
produce that.

### Two substrate defects this found

Unifying and validating the *result* gave 66 violations while raw conformance
sat at 119/119: 40 organizations with no `pulse:ror` (stripped by
`build_jsonld_output` and never re-derived) and 26 articles with no
`pulse:hasDeposit` (minted only by the canonical projection). Raw conformance
could not see either — `RawArticleShape` has no `sh:minCount` there and
`RawOrganizationShape` is open.

So the corpus now supports two different questions, and the second is the one
that matters for a durable layer:

```bash
# is the substrate well-formed?
python scripts/v2/canonical_conformance.py <dir> --layer substrate

# is it sufficient to rebuild canonical from?  (needs a store)
python scripts/v2/unify.py <store-url> --dry-run
```


## Contributions added to the substrate — 2026-09-09

`pulse:Contribution` was excluded from the substrate as a derived edge. It is
not: the count comes from GitHub's `contributions` field, so it is reported.
The exclusion left the store-side canonical graph with **zero** contributions
while `/v2/extract` returned 46, and nothing reported the gap —
`PersonShape` puts no `sh:minCount` on `pulse:hasContribution`.

```
corpus                : 120 identical / 0 changed   (`output` untouched)
substrate             : 46 contributions, 0 stranded, 0/119 slices fail the raw gate
substrate SHACL warns : 0 across 119 pipeline writes
unification           : 670 records -> 633 clusters (was 624 -> 587)
graph:canonical       : 3,278 triples, 0 violations, 46 contributions queryable
```

### The corpus exercises the plumbing, not the aggregation

All 46 counts are **1** and all 46 authors are `urn:pulse:repo-author:...`
stubs — the synthesized repo owners. So `MAX` over a set of identical 1s proves
nothing about the aggregation, and it is fixture-tested instead.

One accidental exception worth recording: a verification store that held two
loads of the same corpus produced 1,340 records → 633 clusters, **all 633
cross-run**, and every contribution's count came out **1, not 2** — the exact
double-counting a `SUM` would have produced. That is the strongest real-data
evidence for `MAX` this corpus can give.

A run with real contributors (`agent_runtime=llm`, or rule_based over a repo
whose contributors resolve) would populate genuine counts and is worth doing
once.
