# Refactor handoff — state as of 2026-09-08 (rev. 2)

Everything a fresh session needs to continue the ontology-first refactor without
re-deriving it. Nothing in this session was committed, as instructed.

**Plan:** https://claude.ai/code/artifact/e4572fb3-cf5f-4883-86fa-8b703f495052 (rev. 6)
**Architecture:** `PROVENANCE_ARCHITECTURE.md` — four layers, store-side unification
**Ontology asks:** `ONTOLOGY_V3_REQUIREMENTS.md` — 6 items, 4 to be carried as patches

---

## 1. Environment — read this before running anything

NixOS host. `flake.nix` is in the repo (staged, not committed) and is the only
supported way in:

```bash
nix develop            # python 3.12 (matches CI), uv, just, jq, curl, git
```

Three things that cost time to discover:

- **Do not set `UV_PYTHON`.** It makes `uv pip install` target the read-only
  nix-store interpreter and the install fails with "consider creating a virtual
  environment". The flake's `shellHook` creates and *activates* `.venv` instead.
- **`gimie` is an undeclared but mandatory dependency.** No live extract works
  without it. It cannot be installed in-process: `gimie==0.7.2` forces
  `python-dotenv` down to 0.21.1, and this repo requires `>=1.2.2`. The sidecar is
  the only supported path — its own Dockerfile says so. Start it with:
  ```bash
  docker build -t gme-gimie-api:0.7.2 tools/gimie-api
  docker run -d --name gme-gimie-api -p 15400:15400 gme-gimie-api:0.7.2
  GIMIE_API_URL=http://localhost:15400 just serve-dev
  ```
  `GIMIE_API_URL` is passed on the command line on purpose — `.env` is not to be
  edited.
- **`.env` is gitignored** (`.gitignore:143`). `git clean -fdx` would delete it.
  Use `git clean -fd` if you ever clean, never `-x`.

`.env` holds `GME_GITHUB_TOKEN`, `RCP_TOKEN`, `API_TOKEN`,
`V2_USE_MOCK_PROVIDERS=false`. Note the var is `GME_GITHUB_TOKEN`, not
`GITHUB_TOKEN` — `config.py:64`.

Qdrant is not running and `data/index` is empty, so RAG degrades. That is fine for
`rule_based` and is recorded in the baseline conditions.

### Shell gotchas in this environment

- `pkill -f "uvicorn …"` **matches its own command line** and kills the wrapper
  shell (exit 144). Use a character class: `pkill -f "[u]vicorn git_metadata_extractor.app"`.
- `ls` is aliased to a detailed lister; `$(ls *.json | head -1)` yields a whole
  `ls -l` line, not a path. Use python `glob` instead.
- `just serve-stop` does not reliably find a running server. Check with
  `ss -ltn | grep :1234`.

---

## 2. What is done

### Phase 0 — behaviour freeze · complete and verified

| Artefact | Path |
|---|---|
| Corpus definition (120 URLs) | `tests/v2/corpus/seeds.txt` |
| Recorded signature (434 entities, post-fix) | `tests/v2/corpus/baseline.signature.json` |
| Findings + run conditions | `tests/v2/corpus/BASELINE_FINDINGS.md` |
| Raw results (gitignored) | `data/corpus/baseline/` |
| Seed generator | `scripts/v2/make_corpus_seeds.py` |
| Signature + differ | `scripts/v2/corpus_signature.py` |

Use it after every phase:

```bash
scripts/v2/batch_extract.sh rule_based tests/v2/corpus/seeds.txt data/corpus/after
python scripts/v2/corpus_signature.py diff \
    tests/v2/corpus/baseline.signature.json data/corpus/after -v
```

Non-zero exit means something changed. Phases 2 and 3 must produce an empty diff.
**The server must be running with `V2_PIPELINE_CACHE_ENABLED=false`** or the
comparison is meaningless — `corpus_signature.py` now refuses such runs.

Signature stability is **verified, not assumed**: 21 of 434 entities are UUID-keyed,
and a re-extract with `V2_PIPELINE_CACHE_ENABLED=false` regenerated one UUID while the
signature stayed byte-identical.

### Phase 1 — deletions · complete

`58,274 → 55,824 LOC`, 1,500 tests pass. Removed `git_metadata_extractor/experimental/`,
`dev/{bug-plans,superpowers,split-rag-indices,security-audit.md,MIGRATION.md}`, and
`src/` + `tests/v1/` (530 stale `.pyc`, no sources).

**Deliberately kept** — do not delete these:
- `dev/ontology-v2-json-response/` — three live tests read it
  (`test_promoted_strict_schemas.py`, `test_promoted_agent_schemas.py`,
  `test_roundtrip.py`), and it holds the `deduplication/` design reference
  (14 `.pyc`, no sources). Phase 2 removes it.
- `docs/releases/v3.0.0/` — this is the **provenance ontology branch**, not a stale
  copy. It has `pulse:Observation`, which PR #25 does not.

### A bug fixed along the way

`providers/github_accounts/users_parser.py::_scrape_orcid_activities` was launching a
visible Firefox on the operator's desktop from inside the server. Three faults:
`SELENIUM_REMOTE_URL` defaulted to `localhost:4444`; failure fell back to
`webdriver.Firefox()`; and `options.headless = True` was a silent no-op (removed in
Selenium 4.23, repo pins ≥4.36). Now matches `selenium_fetch.py`: remote only, real
`--headless`, degrades to `None`.

---

## 3. Decisions — all settled

| Decision | Answer |
|---|---|
| Target architecture | `PROVENANCE_ARCHITECTURE.md`, four layers |
| Oxigraph | in scope for this service to own and write |
| Ontology merge | assume locally as patches, upstream to PR #25 once green |
| Ontology version | v3.0.0 everywhere (code currently loads v2.1.2) |
| Org model | one `org:Organization` + N `OrganizationProfile`; `unitOf` for real hierarchy |
| Harvest platforms | GitHub + GitLab. Others recorded, refused by the classifier |
| Id migration | change in place, bridge with `owl:sameAs` |
| No-LLM mode | survives one release via projection/enrichment split; retire at N+1 |
| `/v2/extract` response | **substrate slice + Phase 9 query API; version the endpoint rather than reinterpret it** |
| Provenance encoding | **RDF-star quoted triples in `graph:prov`** — store-only, see §3c |
| Shape cardinality | **accepted as-is**; generated models expose 14 properties as lists, consumers must not assume scalars |
| Identifier value format | **bare is intentional**, `pulse:ror` stays URL — a confirmed store-wide value migration |

The last two were answered at the end of this session and are the newest.

### The RDF-star decision — ANSWERED in §3c, no spike needed

Oxigraph is RDF-star native, so the store side is fine. The Python side is not
proven: this repo pins `rdflib==6.3.2` and `pyshacl==0.28.1`, and quoted-triple
support there is uneven. **Before Phase 7 writes any provenance code, spike it**:
write one quoted triple and read it back through the pinned stack. If it fails, the
fallback is `pulse:Observation` in plain RDF — the ontology already supports it, and
`graph:prov` is outside the SHACL-validated shape sets either way.

---

## 3b. Phase 3 (partial) and three bug fixes — landed after the first handoff

**Phase 3: all 33 stages are sequenced as data** (see §3b-iv); the graph
wrapper and unified stage signature are not done and move to phase 5.
`pipeline/state.py`, `pipeline/runner.py` and
`pipeline/run.py` exist; 14 of 33 stage call sites have moved out of
`api/extract.py::extract`, which is down from 1,110 to 956 lines. Two chains
are migrated: `ASSEMBLED_CHAIN` (12 stages) and `RECONCILED_CHAIN` (2).
Verified with an empty corpus diff, cache off on both sides.

`fail_open` is declared per stage because only `org_relationships` was wrapped
in the route; the rest let exceptions reach FastAPI as a 500. Do not make the
runner uniformly forgiving — that converts hard failures into silent partial
graphs. Shrinking the fail-open set is phase 8.

**Three handle bugs fixed** (`pulse:githubUsername` /
`pulse:githubOrganizationHandle` are URL-valued; the code treated them as bare
handles inconsistently, in both directions). Results on the 120-URL corpus:
SHACL violations 60 → 10, `schema:author` violations 46 → 0, corrupt
`github.com/https:` values 464 → 0. Detail in
`tests/v2/corpus/BASELINE_FINDINGS.md`.

**`baseline.signature.json` was re-recorded** from the post-fix state (434
entities). It is no longer the pre-refactor freeze.

### Two process lessons that cost real time

1. **The pipeline cache silently invalidates corpus runs.** A cache hit replays
   the whole stored response, including `stats.duration_ms`, so it is
   indistinguishable from a fresh run inside the file. Two verification runs
   were wasted before `corpus_signature.py` grew `_cache_served_fraction`,
   which now refuses majority-cached directories. Always set
   `V2_PIPELINE_CACHE_ENABLED=false`.
2. **Do not trust a test's name.** Two tests named for schema validity never
   called a validator and hardcoded the invalid values, so 1,500 tests passed
   while 39% of extractions shipped a SHACL violation. When touching a
   synthesised entity, assert against `StrictSchemaValidator`.

## 3b-ii. Phase 3 — ROR resolver chain + a timing bug (2026-09-08)

**`RESOLVER_CHAIN`, 4 stages moved.** `resolve_company_to_ror`,
`resolve_bio_to_ror`, `resolve_bio_to_ror_llm`,
`resolve_placeholder_orgs_to_ror` — 146 contiguous lines out of `extract()`,
which is now **1,234 lines** (from 1,366; 1,528 at the start of the refactor).
**19 of 33 stages** now live in `pipeline/run.py` (13 assembled + 4 resolver
+ 2 reconciled); 14 remain inline. *(Superseded — see §3b-iii: 24 of 33.)*

All four were already fail-open in the route, and all four keep it: a ROR RAG
that is down degrades the graph rather than failing the request. Their stage
names match the `_helpers.STAGE_*` constants exactly, which matters because the
runner derives both the warning text (`f"{stage.name} stage failed: {exc}"`)
and the exception log from `stage.name` — a renamed stage silently changes both.

**Verified against the corpus, cache off.** 120 URLs, rule_based,
`V2_PIPELINE_CACHE_ENABLED=false`:

```
identical : 120      cache-served: 0/119 (median wall/reported ratio 1.17)
changed   : 0        434 entities, matching the baseline
```

Evidence the stages actually ran rather than being skipped into a false pass:
the server logged `resolve_company_to_ror` 461 times, `resolve_bio_to_ror` 383,
`resolve_placeholder_orgs_to_ror` 147 across the 119 runs — and
`resolve_bio_to_ror_llm` **zero** times, which is correct: its gate requires the
LLM or hybrid runtime. Sub-second runs are the *provider* cache (content-
addressed external lookups); the pipeline itself executed in full.

### A timing bug in the already-moved stages

`_log` read `state.timings[name]`, but `run_pipeline` writes that entry only
*after* the stage returns — and adapters log while still inside the stage. So
**all 14 previously-moved stages logged `in 0.00s`**, silently replacing the
route's real per-stage durations with zeros. `state.timings` itself was always
correct, so response stats were unaffected; the loss was operator-facing.

Fixed by having the runner publish `state.stage_started_at`. Deliberately a
typed field rather than an `extras` key: `extras` carries values bound for the
response, and a private runner marker does not belong in it (an existing test
asserting `extras` exactly is what surfaced the distinction).
`test_adapters_can_log_their_own_duration_mid_stage` guards it.

### A layering violation, named not fixed

`pipeline/run.py` needs the resolvers' env gates, which live in
`api/_helpers`. Importing that at module scope is a cycle
(`api/__init__` → `api/extract` → `pipeline/run`), so `_gate()` defers the
import. The cycle's direction is the tell: those flags read process
configuration, not requests, so they belong in `config.py`. Relocating them is
its own change — this phase moves the sequence without altering what stages
read.

---

## 3b-iii. Phase 2 done, Phase 3 to 24/33 (2026-09-08)

### Phase 2 — the schema triplication is gone

`AGENTS.md` documented JSON Schemas living in "three byte-identical copies
that must stay in sync". Two of the three are deleted:

| Removed | Why it was safe |
|---|---|
| `dev/ontology-v2-json-response/a-001/json-schema/` (12) | read only by the two `test_promoted_*` tests |
| `tests/v2/fixtures/schema/{strict,agent}/*.schema.json` (12) | read by **nothing** |

**The invariant was already broken.** Four of the agent copies had drifted
from source, missing `pattern` constraints the real schemas carry — so every
test using the `load_schema` fixture was asserting a *laxer* contract than
production enforces. `conftest.py` now points at
`git_metadata_extractor/schema/json/`, the files the service actually opens.

What survives from the deleted tests, in `tests/v2/test_json_schemas.py`: each
schema is valid JSON Schema, and the agent schema is a superset of the strict
schema's property names (a field strict demands but agent omits is
unreachable, since `validate_permissive` soft-drops the unknown).
`test_roundtrip.py` kept its real RDF-fidelity coverage — its fixtures moved to
`tests/v2/fixtures/roundtrip/`, so `dev/` is no longer a test dependency.
`dev/.../deduplication/` is untouched: `PROVENANCE_ARCHITECTURE.md` marks that
`.pyc`-only material for porting.

**The 12 source schemas stay.** They are the live validators —
`load_agent_schema` and `_load_strict_schema` open them per request. They
retire when the generated ontology models replace them as the validation
mechanism, which is v3 adoption, not this phase.

### The bug the deletion exposed

Removing the stale copy made three tests fail against the *real* schemas, all
the same way: a bare identifier where a canonical URL is patterned. Checking
the live corpus rather than assuming it was a mock artefact found **5 real
invalid Person stubs per 120-repo run**.

Cause: `ownership_check.py` has **three** Person-stub producers, and all three
wrote the bare handle into `pulse:githubUsername`. Two were already fixed; the
third — the fork-parent owner emitter (~line 2233) — was the live one. Worth
recording that I first "fixed" the wrong producer and the corpus showed no
change at all; the count only moved once the fork-parent emitter was corrected.
Reasoning from a plausible-looking call site is not evidence.

Result: bare values **5 → 0**, SHACL violations **10 → 5**.
`test_fork_parent_owner_stub_uses_the_canonical_profile_url` pins it.

### Phase 3 — 24 of 33 stages

Two more chains, and the four separate `PipelineState` constructions collapsed
into one threaded state, which is what `state.py` was built for:

| Chain | Stages |
|---|---|
| `RECONCILE_CHAIN` | permissive_validation, llm_dedup, **reconciliation** |
| `RESOLVER_CHAIN` | the four ROR resolvers |
| `REFINEMENT_CHAIN` | llm_critic, refine_with_llm |
| `RECONCILED_CHAIN` | guarantee_repo_author, validate_org_github_handles |
| `ASSEMBLED_CHAIN` | 13 stages |

`extract()` is **1,132 lines** (1,528 at the start of the refactor; 1,366 at
the start of this session). `reconciliation` is the first fail-**closed** stage
moved: a graph that cannot be reconciled has no meaningful partial form, so the
call site re-raises `StageError.cause` to keep the exception type FastAPI saw.

**Verified:** 115 identical / 5 changed, 0/119 cache-served. The 5 are the
stub fix; the 11 newly-moved stages contributed zero diff.

### The 9 stages still inline

`strict_validation`, `assemble_output`, `link_veracity`,
`tag_rule_based_disciplines`, `concept_tagging`, `build_jsonld_output`,
`shacl_gate`, `compute_stats`, plus the `link_veracity` pruning follow-up.

These are not more of the same. `assemble_output` and `build_jsonld_output`
change the payload type (`reconciled` → `assembled` → `payload`), which
`PipelineState` already carries — that part is fine. The obstacle is that
`compute_stats` and the tail build the **HTTP response**: they read a dozen
`extract()` locals and return the response body. Moving them means deciding
whether response construction belongs in `pipeline/`, which is a design
question rather than the mechanical move this phase is scoped to. Recommend
taking it with plan phase 5 (the substrate writer), which reshapes these
stages anyway.

---

## 3b-iv. Phase 3 — the whole sequence is a list (2026-09-08)

Seven chains, **34 stages**, one `PipelineState` threaded through all of them:

| Chain | Stages | Payload |
|---|---|---|
| `RECONCILE_CHAIN` | 3 | `buckets` → `reconciled` |
| `RESOLVER_CHAIN` | 4 | `reconciled` |
| `REFINEMENT_CHAIN` | 2 | `reconciled` |
| `RECONCILED_CHAIN` | 2 | `reconciled` |
| `OUTPUT_CHAIN` | 6 | `reconciled` → `assembled` |
| `ASSEMBLED_CHAIN` | 13 | `assembled` |
| `PAYLOAD_CHAIN` | 4 | `assembled` → `payload` |

34 rather than 33 because four are bookkeeping the route did inline rather than
as named steps (`critic_exclusions`, `assembled_warnings`,
`link_veracity_start`, `permissive_validation` — the last being log-only).

`extract()` measured, `noqa` stripped, before and after:

| | HEAD | now |
|---|---|---|
| file lines | 1,528 | **811** |
| cyclomatic complexity | 98 | **28** |
| branches | 101 | **28** |
| statements | 361 | **133** |

Still above ruff's thresholds, so the blanket `noqa` stays — what remains is
request parsing, cache handling and response construction, not stage
sequencing.

**Verified:** 120 identical / 0 changed, 0/119 cache-served, 0 server-logged
cache hits. Stage-execution evidence from the server log rather than trusting
an empty diff: `strict_validation`, `output_assembly`, `link_veracity`,
`jsonld_build`, `shacl_gate`, `permissive_validation` and `reconciliation` each
ran 119 times, and `rule_based_disciplines` exactly **90** — matching the 90
repository seeds of the 120 (the other 30 are user/org roots), so its gate is
demonstrably discriminating rather than always-on.

### Where the line was drawn, and why

`compute_stats` and everything after `shacl_gate` **stay in the route**. They
build the HTTP response: `V2JSONLDOutput`, the `stage_sequence` list, the
context-summary field, the stats envelope. The pipeline's job ends with a
graph; turning a graph into a response is the API layer's. That boundary is
what makes `pipeline/run.py` importable without FastAPI.

Three things had to move with the stages because they were graph logic living
in the HTTP module: `_iter_reconciled_entities`,
`_root_entity_type_for_detected_type` and `_build_rootless_assembled_output`
are now `_reconciled_entity_payloads`, `_root_entity_type` and
`_rootless_assembled_output` in `run.py`.

### Two behaviours preserved deliberately rather than fixed

- **`assemble_output`'s 422.** `RootEntityValidationError` (a `ValueError`
  subclass, so handler order matters) still becomes a 422 listing the offending
  fields — but the mapping now happens at the chain call site, where HTTP
  status codes belong, not inside the stage.
- **`link_veracity_seconds` measures too much.** The clock is stamped
  unconditionally before the runtime and flag gates, so the figure covers
  link-veracity *plus* the two stages after it, and is non-zero even when
  link-veracity is skipped. That is what the current stats report, so
  `link_veracity_start` reproduces it exactly rather than quietly correcting a
  published number.

### Layering debt, named

`pipeline/run.py` reaches into `api/_helpers` for env gates and
`_jsonld_to_graph`, via deferred imports because `api/__init__` →
`api/extract` → `pipeline/run` is a cycle. The direction is the tell: those
read process configuration and convert JSON-LD, neither of which is HTTP.
They belong in `config.py` and a `pipeline/` utility. Five deferred imports
and one `# noqa: SLF001` mark every spot.

---

## 3c. Phase 2 (partial) — ontology submodule + generator

**Done and verified.**

| Artefact | Purpose |
|---|---|
| `vendor/open-pulse-ontology` @ `290579d` | submodule, pinned to PR #25 |
| `ontology/patches/01-platform-instance.patch` | `pulse:platformInstance` on all 4 profile shapes |
| `ontology/patches/02-same-organization-as.patch` | `pulse:sameOrganizationAs ⊂ owl:sameAs` |
| `ontology/patches/03-coarse-discipline-tiers.patch` | the 46 coarse discipline terms as enumeration instances |
| `ontology/patches/README.md` | the series + how to upstream each as a PR-25 commit |
| `scripts/v2/prepare_ontology.py` | reset-to-pin then re-apply patches, idempotent; `--check` for CI |
| `scripts/v2/ontology_reader.py` | SHACL → typed IR (24 shapes, 253 properties) |
| `scripts/v2/generate_from_ontology.py` | IR → Pydantic, one module per layer; `--check` drift gate |
| `git_metadata_extractor/schema/generated/` | 24 models (raw 12 / canonical 10 / provenance 2), 12 enumerations, 111-term JSON-LD context |
| `just ontology-prepare` · `-check` · `ontology-models-generate` · `-check` | entry points |

Tests: `tests/v2/test_ontology_reader.py` (11),
`tests/v2/test_generated_ontology_models.py` (10),
`tests/v2/test_generated_enumerations.py` (23). All skip cleanly when the
submodule is absent.

### Enumerations — done 2026-09-08

`sh:class` on an enumerated property now produces a `Literal`, not a `str`.
Before this, nothing rejected a `pulse:repositoryType` of `"pulse:Banana"`.

Three things were not what the plan assumed, and each is now encoded in a test:

- **Twelve enumerations, not six.** The plan counted the canonical file. The
  raw file declares six more (Visibility, Modality, SpaceSdk,
  SpaceRuntimeStatus, MembershipType, IdentifierScheme).
- **They are extended across files, so they cannot be generated per layer.**
  `pulse:PublicationTypeEnumeration` is declared with 7 members in canonical
  and gains 8 more from raw. Reading either file alone yields a confidently
  wrong vocabulary, and it matters in practice: the *canonical* `DepositShape`
  has to accept `pulse:SoftwarePublication`, which only the raw file declares.
  So enumerations get one shared `enumerations.py`, unlike the per-layer shapes.
- **`pulse:DisciplineEnumeration` has 1606 members**, past a 64-member
  threshold, so its alias degrades to `str` with the vocabulary exposed as
  `DISCIPLINE_MEMBERS` for runtime checks. A `Literal` that wide bloats the
  module and slows type checkers, for a vocabulary that churns with every EPFL
  Graph refresh.

Each enumeration emits a `Literal` (or `str`), a `frozenset` of members, and —
for the Literal-sized ones — a label map, which is the human-readable half the
hand-written schemas carried as prose.

One bug found and fixed on the way: the reader had no `wd:` prefix, so all 1606
disciplines compacted to full IRIs and matched none of the `wd:Q...` values the
pipeline emits. Six other declared prefixes were missing too (`rdfs`, `skos`,
`dct`, `coar-access`, `coar-resource`, `datacite`); a test now asserts no
member is an uncompacted IRI.

**This surfaced a blocking v3 gap, now patched** —
`ONTOLOGY_V3_REQUIREMENTS.md` §2.5. v3 declares only *leaf* disciplines as
instances: the 41 coarse categories our agent schema offers survived as bare
`rdfs:subClassOf` parents with no `rdf:type`, and the 5 faculty roots were gone
entirely. `sh:class` walks `rdf:type/rdfs:subClassOf*` **from** the value node,
so a parent with no type fails however many children it has — all 46
agent-emittable disciplines were violations. Not a long tail: 100% of the
field.

`ontology/patches/03-coarse-discipline-tiers.patch` restores the two coarse
tiers (1606 → 1652 instances) and all 46 now validate. Still to upstream into
PR #25 with the other two.

### JSON-LD context — generated 2026-09-08, not yet served

`schema/generated/context.jsonld`, 111 terms, from the same IR as the models.
The hand-written file encoded three things the shapes already carry:
`@type: @id` for references, `@container: @set` for multi-valued properties,
`@type: xsd:*` for typed literals. The version stamp comes from the ontology's
own `owl:versionInfo` (`v3.0.0-develop`) rather than the submodule SHA, so the
drift gate does not depend on git state.

**Deliberately not wired in.** `load_jsonld_context()` still returns the v2
file, because adopting the generated one changes the wire format and Phase 3's
corpus diff must stay empty. Two independent reasons, both asserted in
`test_generated_context_is_not_a_drop_in_replacement`:

- **v3 renamed properties.** `pulse:OrganizationType` → `pulse:organizationType`
  (the capital was a v2 irregularity); `pulse:githubRepoStars` /
  `githubRepoForks` → `pulse:repositoryStars` / `repositoryForks`
  (platform-agnostic); `pulse:githubOrgFollowers` and the `affiliations` alias
  are **gone with no replacement**.
- **11 properties gain `@container: @set`** from the accepted loose
  cardinality, so a lone value serialises as a one-element array.
  `test_the_served_context_is_still_the_hand_written_one` is the guard; delete
  it in the phase that adopts v3 output, and re-baseline the corpus.

A second reader bug surfaced here, with the same shape as the `wd:` one — a
constraint the reader could not see:

- Four properties carry their type only in a **property-level `sh:or`**
  (`pulse:ownedBy`, `collectionOwnedBy`, `collectionIncludes`,
  `projectOutput`), e.g. `sh:or ( [sh:class schema:Person] [sh:class
  org:Organization] )`. The reader looked only at direct `sh:datatype` /
  `sh:class` / `sh:nodeKind`, so all four read as untyped and lost
  `@type: "@id"` — which expands the value as a plain literal and silently
  stops the edge being an edge. `pulse:ownedBy` is the one `infer_owners`
  stamps on every extraction. Note the two `sh:or` forms mean opposite things:
  at node level it says which properties *identify* a node, at property level
  which types a *value* may have.

The prefix table now lives once in `ontology_reader.PREFIXES`, shared by the
CURIE compactor and the context emitter. A second copy is exactly how a context
ends up unable to expand the terms its own models emit.

**Still to do in phase 2:** deleting the 36 hand-written JSON Schema files,
gated on retiring the three tests that read `dev/ontology-v2-json-response/`
(`test_promoted_strict_schemas.py`, `test_promoted_agent_schemas.py`,
`test_roundtrip.py`).

### Four asks became two

Reading `src/ontology/ontology-definitions-provenance.ttl` (229 lines, which
the first requirements draft never opened) withdrew two:
`ExtractionRun rdfs:subClassOf prov:Activity` is already upstream at line 40,
and the RDF-star decision is already documented there **with the library
evidence**: pySHACL has no RDF-star support as of 0.40.0 and rdflib cannot
parse Turtle-star as of 7.6.0 — and this repo pins 0.28.1 / 6.3.2. So the
phase-7 spike is answered: **RDF-star is store-only.** Quoted triples never
touch rdflib or pyshacl; the unifier writes via SPARQL `INSERT` and reads via
`SELECT` bindings. `graph:prov` can never be SHACL-validated or serialised to
JSON-LD.

### Two blocking findings for the plan

1. **The shapes are looser than the schemas they replace — 14 properties.**
   `schema:name`, `pulse:ror`, `pulse:orcidIdentifier`, `pulse:repositoryType`,
   `schema:license`, contribution counts/dates: scalar in the hand-written
   schemas, list in the shapes because `sh:maxCount 1` is absent.
   **Accepted as-is (2026-09-08)** — no patch. The cost lands on consumers:
   generated models type these as `list[...]`, so any code reading
   `person["schema:name"]` as a string needs updating. Budget for that in the
   phase that adopts the models.
2. **v3 changes identifier value formats, bare not URL. Confirmed intentional.**
   `pulse:orcidIdentifier`, `pulse:doi`, `pulse:repositoryHandle` are bare;
   only `pulse:ror` stays URL-shaped. Today's schemas require URLs and all 21
   corpus ORCIDs *are* URLs, so v3 is a **value migration** — rewriting every
   ORCID and DOI in the store — on top of the `owl:sameAs` id migration. They
   are independent: one changes node ids, the other literal values. `pulse:ror` keeping the URL form is
   also intentional. See §2.4 of `ONTOLOGY_V3_REQUIREMENTS.md`.

This also reverses an earlier conclusion: the 10 residual SHACL violations are
**not** a producer bug. They are bare values failing v2.1.2's URL patterns —
i.e. producers already emitting v3's format. Leave them; the migration resolves
them.

### Submodule pinning gotcha

`git submodule add` records the **default branch tip**, not what you later
`git checkout` inside the submodule. Staging the intended commit needs an
explicit `git add vendor/open-pulse-ontology`. This was wrong for a while here
(index said `4a17ee8`, worktree said `290579d`), and `prepare_ontology.py` did
not catch it because it read the *committed* tree while the pin lives in the
*index*. Both fixed. If `src/ontology/` appears missing, the cause is usually a
wrong commit, not a missing checkout — the script now says which.

## 3d. Phase A (partial) — RAG provider collapse (2026-09-08)

**3 of 13 providers converted**, and the safety net that makes the rest cheap.

### Why this needed a net before any code moved

The RAG path requires Qdrant, absent in dev and CI. So the corpus differ
reports an **empty diff whether a refactor here is correct or not** — the one
verification this whole refactor has relied on is blind here. Two new test
files replace it:

| File | Tests | Pins |
|---|---|---|
| `test_rag_provider_contract.py` | 65 | class names, `search` signatures, collaborator shapes, where env gating lives |
| `test_rag_single_collection_provider.py` | 14 | collection routing, filter allowlist, thin-key projection, rerank document text |

The contract file was **mutation-tested**: deleting `github_rag`'s env gate and
renaming `ror`'s `scope_mode` parameter each produced exactly one failure.

### Writing it found four wrong assumptions and two real inconsistencies

Every field in that table was read off the code because four obvious guesses
were wrong:

- `OamonitorRagProvider` and `RenkulabRagProvider` — not `OaMonitor` /
  `RenkuLab`. Casing is API.
- **Two collaborator shapes:** eleven providers take
  `(store, embedder, reranker)`, but `ror` and `snsf` take `(store, rcp)`. One
  shared `__init__` cannot cover all thirteen, which the 78-96% textual
  similarity of their `search` bodies completely hides.
- **Two gating layers:** eleven providers gate on their env var inside
  `build_default_provider`; `infoscience` and `ethz_research_collection` ignore
  theirs and are gated by `dependencies.py`. Both were verified live —
  `build_default_provider()` returns a provider for those two with the flag set
  false.
- **Scope parameter names are API.** `scope_mode` on ror/snsf, `collection` on
  openalex/huggingface, `entity_type` on orcid/oamonitor — passed by keyword by
  the agent tools. Unifying them on one name would type-check, pass every
  existing test, and break every caller at runtime.

### The bug the A/B check caught

`github_rag`'s `_rerank_text` was reconstructed from its docstring during the
conversion and came out as `owner/name + description`. The original is
`repo_id + description` (with an `or repo_id` fallback). Every existing test
passed and the corpus diff was empty, because neither exercises the reranker —
it would have silently degraded GitHub RAG ranking.

Caught by A/B-ing each converted provider against its pre-refactor module from
git with a fake store, across 12 combinations (rerank on/off × filters
present/absent), comparing returned hits, recorded store calls **and** the
documents handed to the reranker. All 12 now identical.
`test_github_reranks_on_repo_id_and_description` pins it permanently.

### Line count, honestly

| | lines |
|---|---|
| the 3 converted providers, before | 597 |
| the 3 converted providers, after | 240 |
| `_rag_index.py` (new, shared) | 280 |
| **all `*_rag.py`, HEAD → now** | **3,686 → 3,329 (+280 shared)** |

Net **−77 lines so far**: the shared module's cost is paid up front and the
savings accrue per conversion. The remaining ten are where the phase pays off.

### What remains, grouped by how hard

| Group | Providers | Work |
|---|---|---|
| single-collection + `fetch_records` | zenodo, oamonitor | add a records mixin to `_rag_index` |
| scoped, `embedder` shape | openalex, huggingface, orcid | keep their own `search` signature, delegate to `_search` with a scope→collection map and per-scope thin keys |
| scoped, `rcp` shape | ror, snsf | needs a second `from_config`, or adapt `rcp` into embedder/reranker |
| chunk/record pair | infoscience, ethz_research_collection | largest (457 + 482 lines); also move their gating out of `dependencies.py` |
| bespoke | renkulab, federated | multi-collection fan-out and an aggregator; probably leave alone |

`_search` already takes `collection`, `thin_keys` and `rerank_text` overrides
precisely so the scoped group needs no new shared code — only a spec and a
signature-preserving wrapper each.

---

## 3e. Roadmap correction, and provenance phase 2 started (2026-09-08)

### The plan I had been citing does not exist

I had been numbering work "Phases 4-9" from a plan that lived in chat and was
lost to compaction. Only scattered references survived. **The authoritative
roadmap is the seven-phase list in `PROVENANCE_ARCHITECTURE.md`**, which is
your document:

| # | Phase | State |
|---|---|---|
| 1 | Ontology & schemas | **effectively done, differently** — see below |
| 2 | Extraction emits profiles + `ExtractionRun` | **done** — run descriptor + canonical projection, 119/119 conformant (§3f) |
| 3 | Substrate writer (named graph per source → Oxigraph) | **done** — named graphs + Oxigraph writer, verified live (§3i) |
| 4 | Store-side unifier (profile→Person linker, value selector) | **done** — 0 SHACL violations on `graph:canonical` (§3j) |
| 5 | Provenance writer (RDF-star into `graph:prov`) | **done** — verified live, fixture-driven (§3k) |
| 6 | Validation split (substrate open / canonical closed + **enforcing**) | **done** — gate refuses before the DROP (§3l) |
| 7 | API (query `graph:canonical`, provenance lookup) | **done** — three endpoints, no SPARQL accepted (§3n) |

Two notes on that doc, now partly stale: its phase 1 asks for hand-authored
substrate/canonical JSON schemas kept in "3-copy sync" — the triplication is
gone (§3b-iii) and the models are generated from the shapes instead, which
satisfies the intent. Its `src/v2/` paths are pre-split names.

### Phase 1's open question resolved: no ontology merge needed

The doc says the profile model is in `v3.0.0-develop` and the provenance model
in published `v3.0.0`, and "**neither has both**". Checked against the pin (PR
#25): it has the profile model **and** most of the provenance vocabulary —
`ExtractionRun`, `partOfRun`, `observedOn`, `observationConfidence`,
`firstObservedOn`, `lastConfirmedOn`, `observationCount`, `samePersonAs`.

What it lacks is the reified `Observation` class and `observedFrom` /
`observedProperty` / `observedValue`. That is **not a gap** — it is the
consequence of the recorded encoding decision (§3.2 of the requirements):
RDF-star quoted triples in a store-side `graph:prov` replace reification. The
pin carries exactly the annotation vocabulary a quoted triple needs and omits
the class it does not. So phase 5 is unblocked as designed, and requirements
ask 4 ("port the reified Observation vocabulary") stays withdrawn.

### Phase 2, first increment: the run descriptor

`pipeline/stages/extraction_run.py` + an `extraction_run` stage at the end of
`PAYLOAD_CHAIN`. **This is the first production use of the generated v3
models** — everything under `schema/generated/` had been generated and tested
but unwired since §3c.

Emits a two-node JSON-LD graph: a `pulse:ExtractionRun` (start/end times,
seeds, `extractedBy`) and the `prov:SoftwareAgent` for this build, validated
against `ExtractionRunModel` / `SoftwareAgentModel` on the way out. Both shapes
are `sh:closed`, so a mistyped property fails in the stage that made it rather
than surfacing as a SHACL warning three stages later.

Three decisions worth not reversing by accident, each asserted by a test:

- **It is not in `output["@graph"]`.** Runs belong to the substrate/provenance
  layer; the canonical graph holds what was extracted, not how. It is returned
  as a sibling of `output` on `V2ExtractResponse` (optional field, so older
  consumers and cached responses stay valid).
- **The agent is a sibling node, linked by IRI.** My first version nested it
  under a key named `prov:SoftwareAgent` — a *class* IRI used as a property
  name, which means nothing in RDF.
- **The run IRI is provisional.** Gap 3 of the architecture wants a substrate
  graph IRI to *be* the `ExtractionRun` so `observedFrom → graph` resolves.
  That graph arrives with phase 3, so the run gets `urn:pulse:run:{id}` now and
  `run_iri()` exists so the swap is one function.

**Verified:** corpus 120 identical / 0 changed — which is the point: an
additive response field must not perturb the canonical graph. 119 of 120 runs
carry a descriptor; the one without is `cosmo-epfl`, the baseline's
pre-existing provider failure, which errors before the pipeline starts.

Note on caching: a pipeline-cache hit replays the stored `extraction_run`
rather than minting a new one. That is correct — provenance records when the
data was produced, and a cache hit serves data produced earlier.

### Next in phase 2

The larger half: agents emitting `pulse:hasProfile` → `PlatformProfile`
instead of flat `pulse:githubUsername` / `pulse:orcidIdentifier` fields. That
*is* the v3 output flip you approved folding in here, and unlike everything
above it will produce a large deliberate corpus diff, to be reviewed entity by
entity and re-baselined once.

---

## 3f. Phase 2 complete — the v3 canonical projection (2026-09-08)

`pipeline/stages/canonical_projection.py` + a `canonical_projection` stage,
**gated off** by `V2_CANONICAL_OUTPUT_ENABLED` and returned as
`canonical_output` beside `output`.

### Why gated rather than flipped

The v3 canonical shapes are `sh:closed`, so a projection is either right or it
produces a graph that validates as neither v2 nor v3 — there is no partial
credit. Building it beside the real output made it measurable *before* anything
depended on it: `scripts/v2/canonical_conformance.py` projects every corpus
result and validates it against the actual `ontology-shapes-canonical.ttl`.

That number went **0% → 100% (119/119)** over four fixes, each of which was a
real defect rather than a tuning step:

| Conformance | Fix |
|---|---|
| 0% | measuring wrong — `ont_graph=` alone does not expose enumeration instances to `sh:class`; the live gate unions data + ontology, so the script now uses `SHACLValidator` |
| 50% | v2 strips `pulse:ror` when the id *is* the ROR; v3's identity `sh:or` requires it, so every ROR org had no identity — re-derived from the id |
| 85% | patch 04: RDFS inference makes every DOI/ROR node fail its own closed shape (§2.6) |
| 87% | Infoscience handles come from the *last* path segment; taking the first produced `pulse:platformUsername: ["entities"]`, which satisfies every SHACL constraint while being meaningless |
| 100% | mint `pulse:Deposit` from the Infoscience article identifier — `ArticleShape` requires one, and `schema:datePublished` has no home on the article at all |

434 v2 nodes project to 670: profiles and deposits are new nodes, which is the
model working rather than bloat.

### The bug SHACL could not catch

The `"entities"` handle is worth dwelling on. It was structurally valid —
correct class, satisfied the identity `sh:or`, right datatype — and completely
wrong. Conformance stayed at 87% *because of an unrelated issue*; the garbage
handle contributed no violation at all. It was found by reading the projected
output, not by validating it. A 100% conformance score does not mean the
projection is right, only that it is well-formed.

### A collision worth remembering

`profile_iri` originally omitted the profile *kind*, so a Person's
`PlatformProfile` and an Organization's `OrganizationProfile` for the same
GitHub handle minted the same IRI — and this pipeline routinely produces both
for one org (a Person stub for the repo owner, plus the Organization). The
second node silently replaced the first, leaving the organization pointing at a
node of the wrong class.

### Known losses, not papered over

- `pulse:githubOrgFollowers` has no v3 property. Dropped.
- A Person's `schema:url` has no slot on the closed `PersonShape`. It moves to
  a profile whose host matches, and is otherwise dropped.
- A DOI-only article (no platform record) gets no deposit and stays
  non-conformant, with nowhere for its date either. None exist in the corpus —
  all 26 articles carry an Infoscience identifier — but the pipeline can
  produce one.

### Verified

Gate off: corpus **120 identical / 0 changed** — the preview costs the v2
contract nothing. Gate on: live request returns 7 canonical nodes from 5 v2
nodes, with profiles and back-links correct. **1,694 tests pass** (+27).

### The flip, when you want it

Three things, in order: point `load_jsonld_context()` at the generated context
(deleting `test_the_served_context_is_still_the_hand_written_one`), swap
`output` for the projection, re-baseline the corpus. The conformance number
means the graph will be valid; what it does not tell you is which consumers
break, and §2.4 plus §2.6 of the requirements list the value and property
changes they will see.

---

## 3g. The raw/substrate layer (2026-09-08, end of day)

`pipeline/stages/raw_projection.py` — **written and measured at 119/119
conformant against the raw shapes, but NOT yet wired into the pipeline.**
(Corrected twice after inspecting the emitted Turtle — see §3h.) It is
reachable only from `scripts/v2/canonical_conformance.py --layer raw`. Wiring
it is the first task of phase 3.

**Superseded 2026-09-09 — it is wired, behind `V2_SUBSTRATE_ENABLED`. See §3i.**

### Why it exists: the canonical projection was skipping a layer

`canonical_projection` translated flat-v2 straight into the **closed**
canonical shapes, so anything canonical has no slot for was dropped. I had
recorded that as "known losses in v3". **That was wrong**, and the correction
matters: `RawPlatformProfileShape` declares `pulse:followerCount`,
`pulse:biography`, `pulse:location`, `pulse:company`, `schema:image`,
`schema:url`; `RawRepositoryShape` declares ~60 properties including
`pulse:archived`, `pulse:defaultBranch`, `pulse:openIssueCount`,
`pulse:releaseCount`, `pulse:visibility`, `pulse:gitTagCount`. I had checked
only the canonical shapes before concluding there was no home.

So the raw projection now carries ~20 repository fields and ~12 profile fields
the providers already fetch and the pipeline threw away. 434 flat nodes become
**1,013** raw nodes.

### The shapes answered the architecture doc's open gap 3

`pulse:partOfRun` points at a `pulse:ExtractionOutput`, and
`ExtractionOutputShape` carries `prov:wasGeneratedBy` → `pulse:ExtractionRun`
plus `pulse:platform`. So the unit of substrate is **one platform's slice of one
run**, and that is what a named graph should be named after —
`urn:pulse:output:{run_id}:{platform}`. The doc asked for a convention; the
ontology already had one.

### Four defects found by measuring, not by reading

| | |
|---|---|
| measured on the wrong input | fed the *canonical* corpus into the raw projection; `after-canon` is the last v2-shaped corpus |
| dangling run reference | `prov:wasGeneratedBy` needs the `ExtractionRun` **node in the graph**; a reference alone is a violation, not a forward declaration |
| `partOfRun` stamped everywhere | only RawPerson / RawOrganization / RawRepository / RawArticle declare it; `RawMembershipShape` is closed and rejects it — the anchor belongs to entities a source describes, not to derived edges |
| patch 04 was incomplete | `RawArticleShape` has the same inferred-`schema:identifier` defect; patch 04 now covers both layers |

One mapping was also plain wrong: `_watchers_count` → `pulse:likeCount`
duplicated `pulse:repositoryStars`, because GitHub's `watchers_count` *is* the
star count. `subscribers_count` is real watchers.

---

## 3h. Inspectable artifacts, and what they exposed (2026-09-08)

`scripts/v2/emit_proof_artifacts.py <corpus-slug>` writes three files to
`dev/v3-proof/` from real corpus output, so the layers can be read as Turtle
rather than argued about:

| File | What it is |
|---|---|
| `canonical.ttl` | the graph `/v2/extract` returns today — 35 triples, 7 nodes |
| `substrate.trig` | the raw layer in **named graphs**, one per `pulse:ExtractionOutput` |
| `run.ttl` | the `ExtractionRun` + `prov:SoftwareAgent` |

The TriG is the phase 3 shape without the phase 3 writer: the script stands in
for it, so the output can be checked before Oxigraph exists.

### Reading the artifacts found three things SHACL had not

Raw conformance was already 119/119 when these were generated. All three of
these validated cleanly and were still wrong — the same lesson as the
`"entities"` handle: conformance means well-formed, not correct.

1. **`org:Membership` lost every property.** `_PASSTHROUGH` had no entry for
   it, so the substrate carried `<id> a org:Membership .` and nothing else.
   That validates because `RawMembershipShape` has no required properties.
   Memberships *are* raw facts — ORCID asserts "X was employed at Y" — so the
   properties are now carried.

2. **`pulse:Contribution` was being written to the substrate, and has no raw
   shape at all.** That absence is the architecture, not an ontology gap: a
   contribution is a **derived** edge (we compute commit counts), so it is
   asserted by the unifier into canonical and gets provenance *there*. Writing
   one into the substrate puts a computed fact in the layer reserved for what
   sources said. Now excluded via `_DERIVED_TYPES`. The contrast with
   `org:Membership` — which does have a raw shape — is the ontology encoding
   exactly this distinction.

3. **ROR-identified organizations land in the meta graph.** `ror.org` is not a
   `pulse:PlatformEnumeration` member (nor a `pulse:IdentifierSchemeEnumeration`
   one), so a ROR org gets no platform, hence no `pulse:partOfRun` anchor, and
   falls through to the graph meant for extraction metadata. **Unresolved — see
   the open question below.**

### Open question for the ontology: where do registry-sourced entities live?

A ROR organization is not a platform profile. It arrives from a registry
lookup during extraction, so it *is* source-attributable, but
`pulse:PlatformEnumeration` has no ROR member and neither does
`pulse:IdentifierSchemeEnumeration`. Three ways out, in preference order:

1. `ExtractionOutputShape` leaves `pulse:platform` **optional**, so a
   platformless `ExtractionOutput` for registry lookups is already legal —
   e.g. `urn:pulse:output:{run}:ror`. Needs no ontology change, but the output
   then has nothing saying *which* registry.
2. Add ROR (and possibly DOI/Crossref) to `pulse:PlatformEnumeration`. Cheap,
   but stretches "platform" to mean "source", which the profile shapes assume
   means an account-hosting service.
3. Treat ROR orgs as canonical-only, since the ROR id *is* the identity and
   nothing about them is platform-scoped. Cleanest conceptually; means the
   substrate cannot explain where a ROR name came from.

I would take (1) now and ask upstream about (2), because it is reversible and
does not widen an enumeration on my say-so.

**Answered 2026-09-09: (2).** You chose to add ROR to
`pulse:PlatformEnumeration` — `ontology/patches/05-ror-platform.patch`,
requirements §2.7, §3i below. It is less of a stretch than the framing above
suggests: `pulse:ORCID` is already a member and the ontology itself calls it a
registry. The meta-graph placement is gone; measuring found two *more*
categories of entity stranded there for unrelated reasons (§3i).

---

## 3i. Phase 3 — the substrate writer (2026-09-09)

**Done.** The raw layer is wired in, grouped into named graphs, and written to
Oxigraph. `PROVENANCE_ARCHITECTURE.md` phase 3's exit criterion — "a run
materializes ≥1 named graph in Oxigraph; raw triples byte-preserved" — is met
and verified against a real container, not a mock.

| Artefact | What it is |
|---|---|
| `ontology/patches/05-ror-platform.patch` | `pulse:ROR` in `PlatformEnumeration`; §2.7 of the requirements |
| `pipeline/stages/substrate.py` | routing + N-Quads; promotes `_group_by_output` out of the proof script |
| `store/oxigraph.py` + `store/__init__.py` | the write layer — `providers/` is the read layer |
| `substrate_projection` / `substrate_write` stages | in `PAYLOAD_CHAIN`, both gated, both fail-open |
| `V2_SUBSTRATE_ENABLED`, `V2_SUBSTRATE_STORE_URL`, `..._TIMEOUT_SECONDS` | in `config.py`, **not** `api/_helpers` |
| `gme-oxigraph` in `tools/deploy/docker-compose.yml` | pinned `0.4.11`, internal network only |
| `tests/v2/test_substrate.py` (27) · `test_raw_projection.py` (17) · `test_oxigraph_store.py` (13) · 5 more in `test_api_health.py` | |
| `canonical_conformance.py --layer substrate` | validates the union of the named graphs, and counts stranded entities |
| `AGENTS.md` "Substrate layer" section | the layer as contract |

### Two mechanics were verified before any code was written

Both are the kind of thing a mock will happily confirm while the real thing
returns 400, so they were probed against a live Oxigraph and the pinned rdflib
first:

- **`POST /store` with no `graph` parameter** dispatches each quad to the graph
  its fourth term names, so a run's whole substrate lands in one request. This
  is why `load_nquads` sends no `?graph=`; adding one would silently retarget
  every quad into a single named graph — the entire layer collapsed, and still
  a 204.
- **rdflib 6.3.2 parses nested-`@graph` JSON-LD into a `Dataset`.** So the
  substrate document is real JSON-LD 1.1 named graphs rather than an invented
  `{"@graphs": {...}}` envelope, and the same bytes the response carries
  serialise to N-Quads with no translator at either boundary.

### The stage order is the whole design, and it is not obvious

```
jsonld_build          -> payload = the flat v2 intermediate
substrate_projection  -> reads it   (BEFORE canonical, which overwrites it)
canonical_projection  -> replaces payload in place
shacl_gate
extraction_run        -> mints the run descriptor
substrate_write       -> folds the descriptor into the meta graph, POSTs quads
```

Two constraints pin it from opposite ends. The projection must precede
`canonical_projection` because both project the *same* intermediate and the
canonical one replaces it. The write must follow `extraction_run` because
`ExtractionOutputShape` constrains `prov:wasGeneratedBy` with `sh:class`, which
can only resolve if the run node is in the graph — a reference alone is a
violation, not a forward declaration. That is why it is two stages and not one.

`test_projecting_after_the_canonical_stage_degrades_the_substrate` shows what
the ordering buys rather than asserting the order: run the wrong way round, the
GitHub slice drops from 5 nodes to 3, the person's profile is never minted, and
the person strands in the meta graph with only a name. **Nothing complains** —
the raw entity shapes are open. My first version of that test asserted the
GitHub graph would disappear entirely; it does not, because `_platform_of`
falls back to the id host. Measuring beat predicting again.

### Measuring the output found three defects reading it had not

Raw conformance was already 119/119 before any of this. All three of these
validated cleanly and were still wrong, because **SHACL has no notion of a
graph name** — it validates the union, so a node in the wrong named graph
conforms exactly as well as one in the right place.

| Stranded per corpus run | Why | Fix |
|---|---|---|
| every ROR organization (41 runs) | no platform, so no `ExtractionOutput`, so no anchor | patch 05 + `_REGISTRY_PLATFORM_BY_HOST` |
| 26 articles | the id is a DOI, and `doi.org` is a *resolver*, not the repository holding the record | `_SOURCE_URL_FIELDS` reads `pulse:infoscienceArticleIdentifier` off the flat node |
| 31 memberships | `RawMembershipShape` is closed and declares **no** `pulse:partOfRun` at all | routed with their subject via a reverse index |

The membership case is the sharpest: it has no anchor slot by design (the
existing comment in `raw_projection` is right that the anchor belongs to
entities, not derived edges), so the named graph is its *only* possible
attribution — and `RawMembershipShape` requires nothing, so a membership
stranded beside the `ExtractionRun` conforms perfectly while asserting that "X
was employed at Y" is a fact about the extraction.

Result: **entities stranded in the meta graph, 98 per corpus pass → 0.** The
conformance script now counts them, because that is the one property of this
layer no validator can check.

Note the two directions dependent nodes point. A profile *names* its subject
(`pulse:profileOf`), so it routes by reading the node. A membership is *named
by* its subject (`schema:Person org:hasMembership`), so it needs a reverse
index. Reading only forwards strands every membership.

### The registry decision

You chose **add ROR to `PlatformEnumeration`** over a platformless
`ExtractionOutput`. Worth recording why it is less of a stretch than it sounds:
`pulse:ORCID` is already a member and the ontology describes it as "the ORCID
researcher-identifier registry", and `pulse:Zenodo` is an archive. The
enumeration already reads as "external source we hold a record on".

Two follow-ups for the upstream PR, neither blocking:

- `pulse:ROR` and the existing property `pulse:ror` now differ only in case.
  Legal in Turtle, and the same shape as `pulse:ORCID` beside
  `pulse:orcidIdentifier`, but a real reading hazard.
- ROR is still **absent from `pulse:IdentifierSchemeEnumeration`**, which has
  `pulse:GRID` ("superseded by ROR but still in wide use"), `Ringgold` and
  `ISNI`. Looks accidental; not patched, because nothing needs it yet.

### A cited test that did not exist

`raw_projection`'s module docstring has claimed since it was written that
`tests/v2/test_raw_projection.py::test_every_mapping_target_is_declared_by_its_shape`
checks its mapping tables against the real TTL "rather than trusting this
docstring". **There was no such file.** So five mapping tables had no coverage
at all, three of the shapes they target are `sh:closed`, and a wrong target
would surface only as a SHACL warning on a live run.

It exists now, reads the pinned TTL through `ontology_reader`, and was
mutation-tested: renaming `pulse:biography` to `pulse:bio` and adding
`org:Membership` to `_PART_OF_RUN_TYPES` each produced exactly two failures.
Two lessons, both already in this document in other forms: a docstring citing
a test is not evidence the test exists, and `just lint` covers only
`git_metadata_extractor/` while CI runs no linter at all — so `tests/` and
`scripts/` drift unchecked.

### Live verification: the whole corpus through the real store

Not a smoke test — all 120 seeds extracted through `/v2/extract` with
`V2_SUBSTRATE_ENABLED=true` and `V2_SUBSTRATE_STORE_URL` pointed at
`ghcr.io/oxigraph/oxigraph:0.4.11`. Server log: `substrate_projection` 119
times, `substrate_write` 119 times, **0 failures**. Then queried back out:

```
named graphs                : 304          triples: 4,867
graphs by slice             : github 119 · ror 41 · infoscience 25 · meta 119
distinct ExtractionRuns     : 119
outputs with a dangling run : 0
entities stranded in a meta graph : none
```

Three independent things that number confirms. The slice counts match the
*offline* projection of `after-canon` exactly (github 119 / ror 41 /
infoscience 25), so the pipeline path and the measurement path agree. Every
`ExtractionOutput` resolves to a run node actually present in the store, which
is the `sh:class` constraint the two-stage split exists to satisfy. And nothing
an entity landed in a meta graph.

The query worth keeping is the last one, because it is phase 4's read path and
it already returns data:

```sparql
SELECT ?person (COUNT(DISTINCT ?g) AS ?graphs) WHERE {
  GRAPH ?g { ?person a schema:Person ; pulse:partOfRun ?out }
} GROUP BY ?person HAVING (COUNT(DISTINCT ?g) > 1)
```

Three persons already appear in more than one run's substrate
(`urn:pulse:repo-author:BlueBrain/BlueBrain`, `https://github.com/0NG`,
`https://github.com/foelin`). Cross-run accumulation is the thing no single
`/v2/extract` can see and the reason unification moved store-side — and it is
now observable rather than argued.

**Append-only, demonstrated rather than asserted.** Re-extracting a URL the
store already held took the graph count 304 → **307** — a new github slice, a
new ror slice, a new meta graph — and `https://github.com/ANTsX/ANTs` now
carries `pulse:partOfRun` in **two** distinct named graphs, one per run.
Nothing was overwritten. That property comes from the IRI embedding the run id,
not from anything the writer does, which is why `POST` (merge) rather than
`PUT` (replace) is the semantic that stays correct if it ever changes.

Also verified by hand earlier: the provenance chain resolves in one query
(repository → output → run → seed → agent), and a ROR organization's name
traces to the ROR slice of the run that looked it up. A user-root seed is the
instructive shape — no repositories at all, an ORCID-identified person anchored
to *GitHub* (its profile is a GitHub `PlatformProfile`), its membership riding
along in the same graph, and four articles in the Infoscience slice.

### Verified

| | |
|---|---|
| tests | **1,694 → 1,756 pass**, 2 skipped (+62) |
| substrate conformance | 119/119, 0 stranded, 434 → 1,021 nodes, 2-4 graphs/run |
| raw conformance | 119/119 (unchanged by patch 05) |
| corpus, substrate **on** | **120 identical / 0 changed** — `output` untouched with both stages running and writing (re-verified after the §3j substrate fixes) |
| store | 304 named graphs, 5,087 triples, 119 runs, 0 dangling, 0 stranded |
| ruff | new files clean; package total **1,513 at HEAD, 1,513 now** |
| mypy | **242 errors at HEAD, 242 now** — none from the new modules |

### A cache hit writes nothing to the store

The cache-hit branch returns the stored response *before* the stage chain runs,
so `substrate_write` never executes and the replayed `substrate` field carries
the original run's graph IRIs. Consistent with the same note about
`extraction_run` in §3e — a cache hit produced no new data — but the
operational consequence is sharper here: **accumulating a corpus into the store
needs `V2_PIPELINE_CACHE_ENABLED=false`**, or `refresh` per request. Otherwise
a cached URL looks extracted and is simply absent from the store, with nothing
in the response to say so.

### The corpus differ needs the baseline's *provider* cache too

The first verification run reported **13 changed** and none of it was the
refactor. `V2_PROVIDER_CACHE_PATH` was set to `.cache/v2-rule-based/providers.db`
— the isolation the cache-topology note in `AGENTS.md` recommends — while the
baseline was recorded against the default `.cache/v2/providers.db`. A fresh
provider cache means every external lookup is live, and the diff was exactly
the volatile fields: 9 repositories whose GitHub **fork counts** had moved by
one or two, and 4 whose **ROR fuzzy search** returned a different top hit
(`Ness Technologies` → `Cirrascale`, and three more), each propagating into
`org:unitOf` / `pulse:ownedBy` / `pulse:owns` edges. No structural change, no
`repositoryStars` change, nothing in a stage this phase touched.

Re-run against the baseline's cache: **120 identical / 0 changed**.

So the run conditions in `BASELINE_FINDINGS.md` need a third line beside
"pipeline cache off": **use the same provider cache the baseline used**. The
pipeline cache invalidates a comparison by replaying it wholesale; the provider
cache invalidates it more subtly, by letting the world move underneath it. And
the tell is diagnostic rather than obvious — a *sub*-set of repos changes, and
only in fields that come straight from a live API.

### Where the flag defaults sit, and why

`V2_SUBSTRATE_ENABLED=false`. The layer is additive and nothing reads it until
phase 4, so the default costs nothing and the projection is not free (434 flat
nodes become 1,021). `V2_SUBSTRATE_STORE_URL` unset gives a genuinely useful
middle state: projected and returned, stored nowhere.

Both stages fail open. An unreachable store must not cost a caller a graph, and
the substrate is append-only, so a skipped write loses one run's slice and
nothing already recorded.

### One piece of layering debt paid rather than added

The new flags live in `config.py`, and `pipeline/run.py` imports it **at module
scope**. Every existing gate in that file reaches into `api/_helpers` through a
deferred import because `api/__init__ → api/extract → pipeline/run` is a cycle.
`config` imports only `agents.runtime`, so it is outside that cycle.
`_substrate_applies` is therefore the shape the other gates should have, and it
says so in its docstring — the relocation of the rest is still its own change.

### What phase 4 inherits

- `store/oxigraph.py` has `select()`, `named_graphs()` and
  `graph_triple_count()` already, which is the unifier's read path.
- **RDF-star stays store-only** (§3c). rdflib 6.3.2 cannot serialise quoted
  triples, so the provenance writer cannot go through `load_nquads` — it will
  have to build `INSERT DATA` text. The module docstring records this.
- The run IRI is still `urn:pulse:run:{id}`. Gap 3 of the architecture wanted a
  substrate graph IRI to *be* the `ExtractionRun`; it is now clear that is the
  wrong shape — `pulse:partOfRun` points at a `pulse:ExtractionOutput`, one per
  platform per run, so a run spans several graphs and cannot be one of them.
  `prov:wasGeneratedBy` on each output is the link the doc was asking for.
  **Gap 3 is answered, not deferred.**
- Oxigraph ships **no authentication** and `/store` is writable by anyone who
  can reach it. The compose service is deliberately not port-published. Anyone
  exposing it needs a proxy in front.

---

## 3j. Phase 4 — the store-side unifier (2026-09-09)

**Done.** `graph:canonical` is built from the accumulated substrate and
validates clean against the closed canonical shapes.

| Artefact | What it is |
|---|---|
| `unify/policy.py` | the per-type half: match keys, id promotion, per-property dispositions |
| `unify/cluster.py` | union-find over the keys, with a veto from stronger ones |
| `unify/merge.py` | union / select / drop, and a `Selection` explaining each choice |
| `unify/remap.py` | rewrite references + composite ids after a rename; `stale_references` invariant |
| `unify/runner.py` | read the store, decide, replace `graph:canonical` |
| `store/oxigraph.py::update` | SPARQL Update, for the `DROP` the rewrite needs — and the path phase 5's RDF-star writer will need |
| `scripts/v2/unify.py` | CLI, with `--dry-run` |
| generator emits `MODELS_BY_TARGET_CLASS` + `SINGLE_VALUED_BY_TARGET_CLASS` | per-type cardinality, read rather than re-derived |
| `tests/v2/test_unify.py` (34) · `test_unify_policy.py` (11) | |

### The decision, and why it is the right one for now

You chose **the unifier writes beside extraction's own resolution**, not
instead of it. Both use the same `ORCID → ROR → handle → urn:pulse:{uuid}`
priority, so they agree wherever they saw the same evidence and diverge only
where the unifier saw more; `pulse:samePersonAs` / `pulse:sameOrganizationAs`
carry the divergence, which finally gives ontology patch 02 a consumer.

The alternative — moving resolution out of extraction, as the architecture doc
specifies — cannot be put behind a flag: `canonical_projection` needs resolved
ids to build `output` per request, so there would be no canonical graph until
unification ran. That is a contract change, not a phase.

### The exit criterion was already met, and that was the first finding

The doc's criterion is "two profiles with a shared ORCID collapse to one
canonical Person across two runs". Measuring first showed **extraction already
does that**: substrate ids come from `id_resolution.py`, so two runs seeing the
same ORCID already agree on the IRI and there is nothing to collapse.

The case that genuinely needs a unifier is one step harder: run A knows a
person only by their GitHub handle, run B resolves their ORCID. Two IRIs, one
person, linked only by a shared `pulse:PlatformProfile` — which is exactly what
the profile model was introduced for.
`test_a_github_person_and_an_orcid_person_collapse` pins it. **That case does
not occur in the 120-repo corpus** (0 profiles are claimed by more than one
entity IRI), so it is fixture-tested and said so.

### What the corpus says the payoff actually is

Measured before writing any merge code, over 119 real substrates: 17 entities
appear in more than one run; 44 of their properties agree and 12 differ. Nine
of the twelve are `pulse:partOfRun` — per-run *by design*, not a conflict. The
other three are `pulse:owns`, `org:hasUnit` and `pulse:hasOrganizationProfile`:
**set accumulation, not contested values. There is not one genuine value
conflict in the corpus.**

That reframed the design. `UNION` is where the value is — EPFL's ten units are
scattered across ten runs and only the union knows it has ten, which
`graph:canonical` now shows — and `SELECT` is machinery this corpus cannot
validate. Same blind spot as Phase A's RAG providers (§3d), handled the same
way: purpose-built fixtures, and the gap stated rather than implied by a green
run.

### The dispositions cannot come from the shapes, and that has a price

45 of the 78 canonical properties carry no `sh:maxCount`, and among them
`schema:name`, `pulse:ror`, `pulse:orcidIdentifier` and `schema:license` are
semantically single while `pulse:owns` and `org:hasUnit` are genuinely many. So
`policy.py` classifies all 45 by hand. **This is the accepted-as-is cardinality
decision (§3c) coming due in the phase §3c said would pay for it.**

The asymmetry is the guard: `sh:maxCount 1` *can* settle the other direction,
because a union of a capped property writes a graph the closed shapes reject —
silently, since the gate is warning-only. So the tests assert no capped
property is `UNION`, that every uncapped one has an entry, and that no entry
names a property the shapes dropped.

Writing that guard found a real flaw in my own table on the first run:
`schema:author` is **shape-dependent** — `sh:maxCount 1` on
`pulse:Contribution` (one author) and unbounded on articles and repositories
(many). It is the only such property in the canonical layer, and a
per-property table cannot express it. Fixed by having the generator emit
`SINGLE_VALUED_BY_TARGET_CLASS` and reading the cap **per type**; the runtime
`MergePolicy.disposition(prop, capped=...)` then overrides the table where a
cap applies. Note the first version of the *test* was also wrong — it treated
"capped anywhere" as capped and declared a correct table broken.

### The deepest finding: raw conformance was measuring the wrong contract

Unifying the substrate and validating the result gave **66 SHACL violations**,
none of them in the unifier. Both causes were in `raw_projection`:

| Violations | Cause |
|---|---|
| 40 organizations fail `OrganizationShape`'s identity `sh:or` | `build_jsonld_output` strips `pulse:ror` when the `@id` *is* the ROR, and the raw projection never re-derived it — the canonical projection does (§3f's 50% → 85% fix), the raw one did not |
| 26 articles missing required `pulse:hasDeposit` | the canonical projection mints a `pulse:Deposit` from the Infoscience identifier (§3f's 87% → 100% fix); the raw projection did not, so the substrate had no deposit and no home for `schema:datePublished` either |

**Raw conformance was 119/119 throughout.** `RawArticleShape` puts no
`sh:minCount` on `pulse:hasDeposit` and `RawOrganizationShape` is open, so
nothing in the raw layer objected. The lesson generalises past these two bugs:
*raw conformance says the substrate is well-formed; it says nothing about
whether the substrate is **sufficient** to build canonical from* — and
sufficiency is the requirement that matters, because the substrate is the
durable layer and anything missing there is unrecoverable.

The ROR one had a second, quieter consequence: `pulse:ror` is the *primary*
match key for organizations and it **never fired once** on real data, because
the field was not in the substrate at all. A dead primary key that reports
success is worse than a missing one.

Both fixed in `raw_projection`, both raw-conformant, and both now covered by a
"sufficiency" section in `tests/v2/test_raw_projection.py` that is explicitly
not shape conformance.

A third defect fell out of the deposit fix: `pulse:depositOf` was missing from
`substrate._NAMES_SUBJECT`, so all 26 deposits stranded in the meta graph. The
only symptom was an article's `pulse:hasDeposit` pointing into a *different
named graph* — which SHACL, validating the union, cannot see. The
conformance script's stranded-entity counter caught it, which is the second
time that counter has earned its place.

### Two reporting functions, because they answer different questions

`dangling_references` asks "does anything point at a node that is not here",
which has legitimate answers — an SPDX licence, a Wikidata discipline. It is a
diagnostic. `stale_references` asks "did the remap miss something", which has
exactly one acceptable answer: every IRI in the alias map was an entity in this
batch and is now something else, so a surviving reference to one is broken by
construction. The runner logs the second at error level.

The diagnostic also needed a namespace exclusion to be usable at all: the two
read paths present enumeration members differently — the JSON substrate carries
`"pulse:platform": "pulse:GitHub"` and leans on the context's `@type: @id`,
while SPARQL returns the same triple as a URI — so the report read 55 offline
and 293 from the store for identical data. Excluding
`https://open-pulse.epfl.ch/ontology#` is precise rather than heuristic: no
entity ever lives in the vocabulary namespace.

### Verified

| | |
|---|---|
| tests | **1,755 → 1,806 pass**, 2 skipped (+51) |
| `graph:canonical` SHACL | **0 violations**, 3,094 triples, against `ontology-shapes-canonical.ttl` |
| unification (live store) | 624 records / 185 graphs → 587 clusters, **16 cross-run**, 0 stale — from a store the *pipeline* wrote, not an offline projection |
| corpus, after the substrate fixes | **120 identical / 0 changed** — `output` still untouched |
| idempotency | three consecutive passes, byte-identical fingerprint |
| substrate conformance | 119/119, 0 stranded, 434 → **1,047** nodes (was 1,021; +26 deposits) |
| ruff / mypy | 1,513 / 242 — unchanged from HEAD |

The concrete payoff, in one query against `graph:canonical`:
`https://ror.org/02s376052` (EPFL) has **10 `org:hasUnit` edges**, assembled
from ten separate runs that each knew about one. No single `/v2/extract` can
produce that, which is the whole argument for moving unification store-side.

### What phase 5 inherits

- `merge.Selection` is already the unit to reify: subject, property, winner,
  losers, rule, and the named graph the winner came from. `contested_selections`
  filters to the ones worth recording — single-candidate values are already
  attributed by their graph, which is the "derived only" grain.
- **RDF-star stays store-only.** `store.update` exists precisely because the
  provenance writer cannot go through `load_nquads`: rdflib 6.3.2 cannot
  serialise quoted triples, so `<< s p o >> prov:wasDerivedFrom <g>` has to be
  built as `INSERT DATA` text.
- The corpus produces **zero** contested selections, so phase 5 has nothing
  real to write until either a second platform is harvested or the value
  selector is exercised deliberately. Worth deciding early whether to build it
  against fixtures or to wait for GitLab.
- `pulse:Contribution` is absent from the substrate by design (§3h), so
  `pulse:hasContribution` has no target in canonical. Contributions are the
  unifier's to *assert* — recomputing commit counts across runs — and that is
  not built.

---

## 3k. Phase 5 — the provenance writer (2026-09-09)

**Done, against fixtures by decision.** You chose to build now rather than wait
for a second platform to produce real conflicts, which is the right call for a
reason §3j made concrete: the corpus produces **zero** contested selections, so
waiting means waiting indefinitely on GitLab.

| Artefact | What it is |
|---|---|
| `unify/provenance.py` | RDF-star annotations + the plain `sameAs` triples, as SPARQL text |
| `store/oxigraph.py::update` | already existed for the canonical `DROP`; now the provenance path too |
| `unify/remap.py::_remap_selection` | rewrite what `merge` decided, so an annotation names the triple canonical holds |
| `scripts/v2/unify.py --no-provenance` | isolate a unification problem from a provenance one |
| `tests/v2/test_provenance.py` (33) | |

### Three mechanics probed before any code was written

Same discipline as §3i, and for the same reason — a mock would confirm all
three while the real store returned 400:

- **`INSERT DATA` with `<< s p o >>`** works in `oxigraph:0.4.11`, and the
  ontology's own documented read-back query resolves against it verbatim.
- **The upsert works**: `DELETE`/`INSERT`/`WHERE` with a quoted subject bumped
  `observationCount` 1 → 2, kept `firstObservedOn`, moved `lastConfirmedOn`.
- **`SUBJECT()` / `PREDICATE()` / `OBJECT()` / `TRIPLE()` / `isTRIPLE()`** are
  all supported, which is what makes the prune expressible as one statement
  and the history readable back generically. A quoted triple comes back in
  SPARQL JSON as `{"type": "triple", "value": {subject, predicate, object}}`.

### A latent phase 4 bug the fixtures exposed immediately

`pulse:samePersonAs` / `pulse:sameOrganizationAs` were being written **onto the
canonical node** by `merge.as_jsonld`. Both violate the closed shape:
`PersonShape` and `OrganizationShape` are `sh:closed` with
`sh:ignoredProperties ( rdf:type owl:sameAs )`, and a *subproperty* of
`owl:sameAs` is not the term itself. Measured: one violation per renamed
entity.

`graph:canonical` nonetheless validated at 0 violations through all of §3j —
because the 120-repo corpus never produced a rename, so the property was never
emitted. A closed-shape violation waiting for the first cross-run identity
merge, in code that had a green SHACL run behind it.

The ontology already said where they belong: *"Plain triple in graph:prov, not
a quoted-triple annotation."* Moved there, and `as_jsonld` lost its resolver
argument in the process.

### The bug the prune found, which is the one worth remembering

`merge` records a `Selection` **before** `remap_entities` runs. So a selection
whose winner is a *reference* can name an IRI the remap then replaces — and a
quoted triple need not exist to be annotated, so the annotation attaches to
nothing and nothing errors.

It was visible only because the prune then deleted the orphan on every pass: a
two-run store reported `1 new annotations, 1 reconfirmed` **forever** instead of
a counter that climbs. The symptom was a number that would not move.

Fixed by remapping the selections alongside the properties. That immediately
surfaced a second, subtler thing: after remapping, a loser can equal the
winner — two runs named one entity differently and resolving identity dissolved
the disagreement. Those are no longer counted as contested, because recording
them would print a "conflict" whose winner and loser are identical and write
provenance for a decision nobody made. Both cases are pinned.

### Deliberate omissions, each with a reason

- **No `pulse:observationConfidence`.** The ontology declares it. The only
  thing this pipeline could derive it from is the selection rule, which
  `pulse:observationKind` already states — so a number here would be the same
  information dressed as a measurement. A real one needs per-source
  reliability weights, which nothing has.
- **The ontology's example kind `"single-source"` never appears.** It describes
  a value with one source, which is precisely the case the derived-only grain
  says not to record: it is already attributed by its named graph.
- **Superseded values are pruned, losing their history.** That is the cheap
  corner the cost profile chooses over an append-only audit trail. If a value
  flaps, its counter restarts.

### Escaping, because these values come from arbitrary repositories

Terms are serialised by rdflib (`Literal.n3()`), never by hand — a
`schema:name` holding a quote, a brace or a literal `<< nested star >>` would
otherwise break the generated SPARQL or, worse, alter it. All eight
adversarial literals round-trip through a live Oxigraph unchanged. IRIs are
different: `URIRef.n3()` does **not** escape, so an IRI containing a character
RDF forbids in an IRIREF is refused outright — a dropped annotation is
recoverable by re-running unification, a corrupted one is not.

Worth noting the first version of the escaping test failed on three of four
cases by asserting expected *text*: rdflib wraps a newline-bearing value in
Turtle long-quotes rather than escaping it, and leaves a tab raw. Both are
valid. The test now asserts a parse round trip, which is the actual contract.

### Verified

| | |
|---|---|
| tests | **1,806 → 1,839 pass**, 2 skipped (+33) |
| live two-run fixture | pass 1: 1 annotation + 1 `samePersonAs`; pass 2: **0 new, 1 reconfirmed**, count 1 → 2, `firstObservedOn` held |
| the ontology's read-back query | resolves verbatim: name → output → rule → count → first → last |
| `samePersonAs` in `graph:canonical` | **0** (it is in `graph:prov`) |
| adversarial literals | 8/8 round-trip through the store, `<< nested star >>` included |
| `graph:canonical` SHACL | still **0 violations**, 3,094 triples |
| real corpus | **0 annotations — `graph:prov` is empty**, which is the honest result and the whole reason for fixtures |
| ruff / mypy | 1,513 / 242 — unchanged from HEAD |

### What phase 6 inherits

Phase 6 is the validation split: substrate open at write, canonical closed
**and enforcing** after unify. Most of the ingredients exist —
`canonical_conformance.py` validates all three layers and
`validate_canonical.py`-style checks run against a live store — so the work is
mostly deciding *where* the gate lives and what a failure does.

Two things to know going in:

1. **`graph:prov` can never be part of it.** pyshacl 0.28.1 cannot validate a
   quoted triple, so the provenance graph is structurally outside every shape
   set. That is recorded upstream too, in the shapes file's own header.
2. **The canonical gate is already green** — 0 violations over the 587-entity
   corpus graph — so making it enforcing costs nothing today. The five residual
   violations noted in §5 are on the *v2* pipeline path, not this one.

Still unbuilt from phase 4: **contributions**. `pulse:Contribution` is absent
from the substrate by design (§3h), so `pulse:hasContribution` has no target in
canonical. Contributions are the unifier's to *assert* — recomputing commit
counts across accumulated runs — and nothing does it.

---

## 3l. Phase 6 — the validation split (2026-09-09)

**Done.** `validation/layers.py` is the one place that knows which shape set
describes which layer, and which layer refuses versus reports.

| Artefact | What it is |
|---|---|
| `validation/ontology.py::load_raw_shapes_graph` | the raw layer's five files, cached; there was only a canonical loader |
| `validation/layers.py` | the layer↔shapes pairing, `validate_substrate`, `enforce_canonical` |
| `unify/runner.py::write_canonical(enforce=True)` | the gate, **before** the `DROP` |
| `pipeline/run.py::_write_substrate` | the reporting gate, behind `V2_SUBSTRATE_VALIDATE` |
| `scripts/v2/unify.py --no-enforce` | publish a failing graph deliberately; exit 3 on a refusal |
| `tests/v2/test_validation_layers.py` (8) + 3 in `test_substrate.py` | |

### The pairing is the thing, and getting it wrong is silent

Validating the substrate against the **canonical** shapes reports the raw
layer's deliberate openness as violations. Validating canonical against the
**raw** shapes checks almost nothing, because the raw entity shapes require
almost nothing. Neither direction errors; both produce a number that looks
like an answer. `ontology-shapes-raw.ttl`'s own header warns about the cause —
`sh:targetClass` applies across the whole shapes graph, so a looser raw
`PersonShape` and a closed canonical one in one graph validate every person
against both — which is why `load_raw_shapes_graph` is a separate graph rather
than a longer file list.

`test_the_substrate_accepts_what_canonical_would_reject` pins it by running the
*same nodes* through both and asserting opposite verdicts.

### Why the severities differ, which reads as inconsistent from outside

- **Substrate: report.** It is append-only and the only durable copy of what a
  run found. Refusing a slice because one entity is malformed loses the other
  entities in it permanently, and the raw shapes are open precisely so a source
  can assert something canonical has no slot for.
- **Canonical: refuse.** It is a pure function of the substrate, rewritten every
  pass, and the one consumers query. Refusing costs one re-run and leaves the
  previous valid graph answering.
- **The in-request `shacl_gate` stays warning-only**, and that is not an
  oversight. There the alternative is returning nothing to a caller who asked
  for a graph. Store-side the alternative is leaving a valid graph in place.
  Different trade, different answer — worth saying out loud, because a future
  reader will otherwise "fix" one to match the other.

### The order is the whole gate

`write_canonical` validates **before** it `DROP`s. Verified live rather than
argued: publish a valid graph (3 triples), then attempt an invalid one — the
gate refuses, `graph:canonical` still holds its 3 triples and still answers the
query, and `enforce=False` publishes it anyway. Validate after the `DROP` and a
bad pass leaves nothing at all.

### This gate is the sufficiency check, which is the point I had flagged

§3j's warning was that per-layer conformance cannot see whether the substrate
holds what canonical *needs* — it was 119/119 raw-conformant while missing two
things `ArticleShape` and `OrganizationShape` require. A "validation split"
that only checked each layer against its own shapes would have caught neither.

Building canonical from the substrate and validating the result is exactly what
`write_canonical(enforce=True)` does, so the sufficiency check is not a
separate gate to build — it is the canonical gate, provided it runs on the
unifier's output rather than on the in-request projection. Recorded in the
module docstring so it does not get "optimised" into validating the projection
instead.

### Two fixtures that were wrong before the code was

Both failures were mine, and both were the same rule: a `sh:class` constraint
needs its target **in the graph**, not merely referenced.

1. A substrate fixture referenced `urn:pulse:output:...` without including the
   `ExtractionOutput` node. Same rule that made the substrate writer carry its
   run descriptor (§3g).
2. `test_the_real_substrate_slice_conforms` validated the output of
   `substrate_projection` alone — which is *legitimately* non-conformant,
   because `extraction_run` mints the run descriptor later and
   `substrate_write` folds it in. That is precisely why the validation call
   sits in `substrate_write` and not in the projection stage, and the test now
   runs all three stages.

Worth keeping: the intermediate being invalid is not a defect, it is the reason
the gate is where it is.

### Verified

| | |
|---|---|
| tests | **1,839 → 1,850 pass**, 2 skipped (+11) |
| substrate gate, real corpus | **0/119 slices fail** the raw shapes |
| canonical gate, real corpus | passes; 587 entities, 3,094 triples published |
| gate refuses correctly | invalid graph rejected, previous 3 triples intact and still answering |
| `--no-enforce` | publishes the failing graph, `samePersonAs` count 0 → 1 |
| ruff / mypy | 1,513 / 242 — unchanged from HEAD |

### What phase 7 inherits

Phase 7 is the API: a query endpoint over `graph:canonical` and a
provenance-lookup endpoint (`triple → source graph / run / confidence`).

The read paths already exist. `store.select` is generic, the ontology's
documented provenance query works verbatim (§3k), and `store.named_graphs` /
`graph_triple_count` cover the inventory. What phase 7 has to decide is the
HTTP shape, and one thing is worth settling first: **the architecture's
decision table says "substrate slice + Phase 9 query API; version the endpoint
rather than reinterpret it"**, so the query API is a *new* endpoint, not a
change to `/v2/extract`.

Two things to carry over:

- **`graph:prov` cannot be returned as JSON-LD.** rdflib cannot serialise a
  quoted triple, so a provenance-lookup endpoint has to answer from SPARQL
  bindings — which is what `store.select` already returns. Do not try to build
  a JSON-LD response for it.
- **Oxigraph has no authentication.** `/store` is writable by anyone who can
  reach it, which is why the compose service is not port-published. A query
  endpoint that proxies user-supplied SPARQL would need to refuse updates
  explicitly; the safe shape is fixed queries with bound parameters.

Still unbuilt, and now the largest hole in the canonical graph:
**contributions**. `pulse:Contribution` is absent from the substrate by design
(§3h), so `pulse:hasContribution` has no target. Contributions are the
unifier's to *assert* — recomputing commit counts across accumulated runs —
and nothing does it. The canonical gate does not catch it because
`PersonShape` puts no `sh:minCount` on the property.

---

## 3m. Contributions — a recorded decision reversed on evidence (2026-09-09)

**Done.** `graph:canonical` now carries contributions. It had **none**, while
`/v2/extract` returned 46 — a divergence between the two canonical graphs that
nothing reported, because `PersonShape` puts no `sh:minCount` on
`pulse:hasContribution`.

| Artefact | What it is |
|---|---|
| `ontology/patches/06-raw-contribution-shape.patch` | `RawContributionShape`; requirements §2.8 |
| `raw_projection._DERIVED_TYPES` | now **empty**; the type set stays, its member was wrong |
| `raw_projection._project_contribution` + `_PASSTHROUGH` entry | |
| `substrate._NAMES_SUBJECT` += `pulse:contributionTo`, `schema:author` | routing, since the shape has no anchor |
| `unify.policy.Disposition.MAX` / `.MIN` | a third kind of merge — see below |
| `unify.merge._aggregate` + `RULE_AGGREGATED` | |
| 5 tests in `test_raw_projection.py`, 6 in `test_unify.py` | |

### The premise was wrong, and it was checkable

§3h excluded contributions from the substrate because *"a contribution is a
**derived** edge (we compute commit counts)"*. We do not compute them:
`agents/rule_based/contribution_agent.py:201` reads GitHub's `contributions`
field. The count is something a platform **reports**, so it belongs in the
layer that records what sources said. What *is* derived is the aggregate across
platforms and runs.

Three facts made it unarguable, and all three were already in the repo:

1. **`RawPersonShape` declares `pulse:hasContribution` with
   `sh:class pulse:Contribution`.** The raw layer expects these nodes to exist.
2. **No raw shape targeted the class.** So `pulse:Contribution` was the one
   entity type the raw layer referenced and could not validate — an ontology
   gap, not a design decision. Note it would not have *failed*: `sh:class`
   resolves because the class is declared, so a contribution in the substrate
   was merely unshaped. Worse than a violation, being invisible.
3. **Canonical `ContributionShape` requires `pulse:contributionCount`**
   (`sh:minCount 1`). So the unifier could not assert an edge without the
   count, and an edge-only contribution would now fail the enforcing gate
   (§3l). With the count absent from the substrate there was simply nothing to
   assert.

That is the third instance of the same class of defect — after the missing
`pulse:ror` and `pulse:hasDeposit` in §3j — and the pattern is now clear
enough to state as a rule: **whenever the canonical projection has to add
something the flat form does not carry, check whether the raw projection adds
it too.** The canonical projection mints deposits and re-derives ROR; the raw
one did neither, and here the flat form *had* the data and the raw layer threw
it away.

### `MAX`, not `SUM`, and the reasoning matters

Merging a count across runs is neither a union nor a choice between sources, so
it needed a third disposition. The intuitive one is a sum, and it is wrong:
`pulse:contributionCount` is GitHub's **running total** for a person on a
repository, so two runs a week apart report 40 and then 43 — not 40 and 3 more.
Summing them produces 83, a number with no referent.

Summing across genuinely distinct *platforms* would be defensible, but the
merge layer sees a cluster without knowing which slice each value came from.
`MAX` under-counts a multi-platform contributor and never over-counts anyone,
which is the safer error for a figure people read as effort. `firstContribution
Date` is `MIN`, `lastContributionDate` is `MAX` — an observed range only ever
widens.

Mixed value types fall back to `SELECT` rather than being coerced: a count that
arrived as `40` from one source and `"43"` from another cannot be ordered, and
`max` over two strings is lexical, so `"9"` would beat `"40"`.

### Two things the change turned up

- **The cap override was logging at ERROR 46 times per pass.** `schema:author`
  is `sh:maxCount 1` on `pulse:Contribution` and unbounded on articles and
  repositories, so the runtime override fires on every contribution — correct
  behaviour, logged as an error. Now `debug`, with the reasoning that
  `test_no_always_capped_property_is_unioned` is the guard against a genuinely
  wrong table and this branch cannot tell the two cases apart.
- **`RawContributionShape` deliberately requires nothing**, unlike the
  canonical shape's three `sh:minCount 1` properties. Same asymmetry as the
  validation split: the substrate is append-only and the only durable copy, so
  it must never refuse a slice for an incomplete assertion.

### Verified

| | |
|---|---|
| tests | **1,850 → 1,860 pass**, 2 skipped (+10) |
| substrate | 46 contributions carried, 0 stranded, **0/119** slices fail the raw gate |
| unification | 670 records → 633 clusters (was 624 → 587), **46 Contribution clusters** |
| canonical gate | passes; **3,278 triples** (was 3,094), contributions queryable |
| raw shape inventory | 12 → **13** — both count assertions updated, with the patch named |
| corpus | see below |

The queryable result, which is the point:

```sparql
SELECT ?author ?repo ?n WHERE {
  GRAPH <urn:pulse:graph:canonical> {
    ?c a pulse:Contribution ; schema:author ?author ;
       pulse:contributionTo ?repo ; pulse:contributionCount ?n .
  }
}
```

46 rows, where before there were none.

### One thing to know about this corpus

All 46 counts are **1**, and all 46 authors are `urn:pulse:repo-author:...`
stubs — the synthesized repo owners `_person_fanout_contexts` materialises.
So the corpus exercises the plumbing but not the aggregation: `MAX` over a set
of identical 1s is indistinguishable from anything else. The aggregation is
fixture-tested, same as the value selector and for the same reason.

A run with real contributors — `agent_runtime=llm`, or rule_based over a repo
whose contributors resolve — would populate genuine counts. Worth doing once to
see a `MAX` fire on real data.

---

## 3n. Phase 7 — the query API. All seven phases done (2026-09-09)

**Done.** Three endpoints over the accumulated store, and the roadmap in
`PROVENANCE_ARCHITECTURE.md` is complete.

| Endpoint | Answers |
|---|---|
| `GET /v2/graph/status` | what the store holds — graphs, canonical vs prov triples, runs, entities by type |
| `GET /v2/graph/entity?iri=…` | one canonical entity, as JSON-LD |
| `GET /v2/graph/provenance?subject=…[&property=…]` | for each *chosen* value: output → run → rule → counters, plus the `sameAs` links |

| Artefact | What it is |
|---|---|
| `api/graph.py` | the three endpoints |
| `store/terms.py` | SPARQL term serialisation + the prefix table, moved out of three copies |
| `api_models/contracts.py` | `V2GraphStatusResponse`, `V2GraphEntityResponse`, `V2ProvenanceRecord`, `V2GraphProvenanceResponse` |
| `tests/v2/test_graph_api.py` (14) · `test_store_terms.py` (36) | |

### New endpoints, not a reinterpretation

The decisions table said *"substrate slice + Phase 9 query API; version the
endpoint rather than reinterpret it"*, so `/v2/extract` still means "extract
this URL now" and these mean "tell me what the store knows". Both are things a
caller needs to be able to ask separately.

`/v2/graph/entity` returns the **same JSON-LD shape** `/v2/extract` returns for
the same entity, deliberately: a caller reading an entity out of the store and
one reading it out of an extraction should not need two parsers.
`/v2/graph/provenance` cannot be JSON-LD — `graph:prov` holds quoted triples
and the pinned rdflib cannot serialise one — so it answers from SPARQL
bindings. Permanent, not a stopgap.

An unconfigured store answers **503, not 404**: "nothing about that IRI" and
"no store" are different answers and a caller that cannot tell them apart will
cache the wrong one.

### The security property, and why it is an assertion about an absence

Oxigraph ships **no authentication** and its `/store` endpoint is writable by
anyone who can reach it — which is why the compose service is not
port-published. So an endpoint that proxied a caller's query string would be an
unauthenticated write primitive one `INSERT` away.

**No endpoint accepts SPARQL.** Every query is fixed text with IRIs
substituted through `store.terms.iri_term`, which *refuses* anything RDF
forbids in an IRIREF rather than escaping it, because `URIRef.n3()` does not
escape. A caller's bad IRI is a 400.

`test_no_endpoint_accepts_a_sparql_string` asserts this over the **whole route
table** rather than per endpoint, so a new endpoint with a `query` parameter
fails without anyone remembering to add a test. Mutation-tested: adding
`query: str | None` to `/v2/graph/entity` produced exactly that failure. The
companion test tries `https://x/a> } ; DROP GRAPH <urn:pulse:graph:canonical> #`
as an `iri` and gets a 400.

### The bug the enforcing gate caught, one phase after it was built

The first two-run fixture would not publish: `CanonicalValidationError` on
`schema:email`, which the closed `PersonShape` does not declare — v3 puts a
person's email on their `pulse:PlatformProfile`.

The cause is structural and was worth finding: **the unifier reads the
substrate, where the entity shapes are open, and copied every property
through.** So any substrate property canonical has no slot for would publish an
invalid graph. The 120-repo corpus never triggered it only because the raw
projection happens to put emails on profiles already.

Fixed by filtering to `canonical_properties_for(entity_type)`, read from the
generated models rather than hand-listed. Dropped properties are counted in
the report (`dropped_properties`), because a property dropped for *every*
entity of a type means the projections disagree rather than that the data is
odd. Nothing is lost: the substrate keeps them, which is the layering working.

Note this is the gate from §3l earning its place a phase later — it refused to
publish, named the property, and left the previous graph intact.

### A second cited-but-missing test, found by reusing the code

`unify/runner.py` carried a prefix table with the comment *"A test asserts this
table is a subset of that one, so the two cannot drift into disagreement."*
There was no such test — the same mistake `raw_projection.py` had made about
`test_every_mapping_target_is_declared_by_its_shape` (§3i).

Consolidating three copies of the table into `store/terms.py` made the test
real: `test_the_compact_prefixes_are_a_subset_of_the_readers` compares against
`ontology_reader.PREFIXES`, which is the authority. Two instances of this
mistake in one refactor is enough to distrust any docstring that names a test —
**grep for it before believing it.** Package ruff findings went 1,513 → 1,511
from the dedup.

### Verified

| | |
|---|---|
| tests | **1,860 → 1,910 pass**, 2 skipped (+50) |
| live, all three endpoints | status / entity / provenance all answer against a real store |
| the provenance chain | `schema:name='Jane Doe'` → output `run-b:github` → run `urn:pulse:run:run-b` → rule `most-complete-source`, plus `sameAs` |
| error paths | 400 bad IRI · 404 unknown entity · 401 no token · 503 no store |
| OpenAPI | all three paths published, so `/docs` documents them |
| injection guard | mutation-tested |
| ruff / mypy | **1,511** (from 1,513) / 242 — unchanged |

### What is left, now that the roadmap is done

Nothing in `PROVENANCE_ARCHITECTURE.md`. The open items are the ones outside
it, in §5, plus two this phase suggests:

1. **Nothing writes to the store in production.** `V2_SUBSTRATE_ENABLED` is
   off by default and `V2_SUBSTRATE_STORE_URL` is unset, so the graph endpoints
   answer 503 on a default deployment. Turning the substrate on for a real
   corpus run and then unifying is the next operational step, not a code one.
2. **Unification has no trigger.** `scripts/v2/unify.py` is a CLI, by choice
   (§3j). Something has to call it — a cron, a post-batch hook, or an endpoint.
   An endpoint is the obvious next increment and is deliberately *not* built:
   it needs a decision about whether unification is synchronous, a job, or
   idempotent-on-demand, and the answer depends on how large the store gets.

---

## 4. Next action

Mind which numbering is in play: the authoritative roadmap is the seven-phase
list in `PROVENANCE_ARCHITECTURE.md` (§3e), and §4.2 below scores the *older*
refactor-plan phase 3, which was about stage sequencing. Two different threes.

### The roadmap is done. What is left is operational, and §5

All seven phases of `PROVENANCE_ARCHITECTURE.md` are complete — §3c, §3f, §3i,
§3j, §3k, §3l, §3n — and the contributions hole is closed (§3m). Nothing in
that document remains.

**The two things that would make it real**, neither of which is a code problem:

1. **Nothing writes to the store in production.** `V2_SUBSTRATE_ENABLED`
   defaults off and `V2_SUBSTRATE_STORE_URL` is unset, so a default deployment
   has an empty store and the graph endpoints answer 503. Turning the substrate
   on for a real corpus run — ideally `agent_runtime=llm`, which produces
   genuine contributors and therefore genuine contribution counts — and then
   unifying, is the next step. It would also give the aggregation and the value
   selector their first real exercise; today both are fixture-tested because
   the rule-based corpus produces zero contested values and 46 identical
   counts of 1.
2. **Unification has no trigger.** `scripts/v2/unify.py` is a CLI by choice
   (§3j). Something has to call it: a cron, a post-batch hook, or an endpoint.
   An endpoint is the obvious increment and is deliberately not built, because
   it needs a decision this session had no basis for — whether unification is
   synchronous, a background job, or idempotent-on-demand — and the answer
   depends on how large the store gets.

Then the §5 list, which is now the largest body of known work: CI runs no
linter and no type-checker, `batch_extract.sh` 401s silently without a token,
and `users_parser.py` has ~20 `print()` calls inside a server process.

**Phase A** (the RAG provider collapse) is also still 3 of 13 in — see §3d for
the pattern and the safety net. It is untouched by any of this.

### 4.2 The older refactor-plan phase 3, scored

The sequencing half is done; the domain-model half is not. Scored against the
original criteria rather than declared finished:

1. ~~Add `domain/graph.py`: graph wrapper with a real IRI type and node access.~~
   **Not done.** The four payloads are still dicts and dataclasses. Deliberate:
   swapping them for one graph rewrites every stage's internals, which is the
   opposite of a move whose value is an empty diff. Belongs with plan phase 5.
2. **Partly.** `Stage(name, run, applies, fail_open)` with
   `run: PipelineState -> None` replaced 33 bespoke call shapes, so the
   sequence is now a list. But it is not `(Graph, Context) -> Graph`, and the
   21 `*StageResult` dataclasses still exist — the stage functions return them
   and the adapters unwrap. Collapsing those needs (1).
3. **Done.** All 33 stages are ordered lists in `pipeline/run.py`; no stage
   logic changed (corpus: 120 identical / 0 changed).
4. Exit criteria: `extract()` under 150 lines — **not met** (the file is 811,
   `extract()` itself ~500; what is left is request parsing, cache handling and
   response construction). `stages/__init__.py` exports the protocol instead of
   50 names — **not done**, it still exports the individual functions the
   adapters import. Corpus diff empty — **yes**. 1,500 tests still pass —
   **yes**: 1,578 then, 1,756 now.

So the honest state: **`extract()` no longer owns the pipeline**, which was the
point. The remaining two items are domain-modelling, and phase 3 made the case
for them concrete rather than weaker: `PipelineState` now threads *five*
payloads, because the substrate is a fifth projection of the same intermediate.
A real graph type with named-graph support would collapse `payload` +
`substrate` into one value and delete `substrate.py`'s serialisation entirely.

**Phase A** is 3 of 13 providers in — see §3d for the pattern, the safety net
and the remaining groups. The agent-tool half (14 `agent_tools/*_rag.py`) has
not been touched at all.

**Phase 2** is complete except for retiring the 12 source JSON Schemas, which
is blocked on v3 adoption rather than on any Phase 2 work — see §3b-iii.

---

## 5. Things worth fixing that are not in any phase

- `users_parser.py` uses `print()` in ~20 places inside a server process. One of them
  was masking the browser bug.
- `gimie`'s sidecar-only requirement is documented nowhere outside
  `tools/gimie-api/Dockerfile`. It belongs in `CLAUDE.md` and the README.
- The 14 SHACL pattern violations (9 `pulse:githubUsername`, 5
  `pulse:githubRepositoryHandle`) are a real data-quality signal — either producers
  emit values the ontology forbids, or the patterns are too strict. Triage before
  Phase 8 turns the gate enforcing.
- `docs/v2-pipeline.md` and `docs/v2-api-reference.md` document the `idSource`
  hierarchy as contract. Both need updating when Phase 6 re-identifies entities.
  Their stage tables were also missing `canonical_projection` and
  `extraction_run` entirely — the v3 flip landed without touching them. Added
  in phase 3 along with the substrate rows, but assume more drift.
- **CI runs no linter and no type-checker.** `.github/workflows/ci.yml` runs
  tests only, and `just lint` / `just type-check` cover
  `git_metadata_extractor/` alone — so `tests/`, `scripts/` and the justfile
  recipes drift unchecked. Current unenforced baseline, for whoever wires it
  up: 1,513 ruff findings (mostly `N815` in generated models) and 242 mypy
  errors, of which the large majority are the `| None` payload fields on
  `PipelineState` reaching stage functions that declare non-optional
  parameters. Fixing that one pattern would clear most of the mypy list.
- `scripts/v2/batch_extract.sh` needs `API_TOKEN` and `jq` in its environment
  and says so in a comment, but a bare `bash scripts/v2/batch_extract.sh`
  silently 401s on all 120 repos and reports `FAIL submit` — nothing points at
  the token. `just` supplies `.env` via `set dotenv-load`; the script does not
  load it itself. Either have it source `.env` or fail once with a clear
  message instead of 120 times with an opaque one.

## 6. Backup

Everything at risk was copied to the session scratchpad before Phase 1:
`…/scratchpad/pre-phase1-backup/` (90 files). If that is gone, the tracked deletions
are recoverable from git history; the untracked items in it were
`PROVENANCE_ARCHITECTURE.{md,html}`, `provenance-dedup.html` and the
`deduplication/` prototype.

There are also **7 pre-existing stashes** on this branch that predate this session.
They were not touched.
