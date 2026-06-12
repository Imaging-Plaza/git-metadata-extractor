# Bug 07 — stub=true on fully-populated entities (ambiguous semantics)
**Severity:** low (data quality / consumer confusion) · **Status:** Investigated — plan ready (no code changed) · **Area:** entity lifecycle

## Symptom
A `stub` marker (the internal `_stub` field, surfaced as `gme-internal:stub` in the
JSON-LD output when `?include_internal_fields=true`) is reported as appearing on
repos/persons/orgs that look fully populated, so a consumer cannot use it to tell a
"thin reference / placeholder" apart from a "fully extracted entity."

The field is named `_stub` in source (not `stub`). At output it is renamed to the
`gme-internal:stub` IRI term (`https://openpulse.science/git-metadata-extractor#stub`)
by `_internal_term` / `_rewrite_internal_keys`
(`src/v2/pipeline/stages/jsonld_build.py:139-142`, `:178-188`, `:245-250`). When
`include_internal_fields=false` (the default) it is dropped entirely
(`_drop_internal_keys`, `src/v2/pipeline/stages/jsonld_build.py:130-136`,
`:311`), so the symptom is only visible with `include_internal_fields=true`.

## Current stub lifecycle (verified, file:line)
`_stub: True` is written in exactly **three** places, all in
`src/v2/pipeline/stages/ownership_check.py`, and is **never read, cleared, or set
false anywhere in the codebase** (`grep -rn '_stub' src/` confirms: 3 writes + 1
docstring mention `:2108`, zero reads, zero `False`/`pop`/`del`):

1. `demote_github_props_to_units(...)` — `src/v2/pipeline/stages/ownership_check.py:2037-2050`.
   When a ROR-parent org carries a GitHub-org handle but has no matching child unit,
   a **freshly synthesized** minimal `org:Organization` unit dict is built and stamped
   `_stub: True` (`:2049`). Note the sibling "reuse" branch at `:2032-2035`
   (`matched_child = id_index[synthesized_id]`) reuses an existing — possibly
   fully-fetched — org and does **not** stamp `_stub`. So this site does not, by
   itself, mark a full entity.

2. `emit_fork_parent_stubs(...)` — Person stub at
   `src/v2/pipeline/stages/ownership_check.py:2153-2167` (`_stub: True` at `:2165`).
   A minimal `schema:Person` for the fork-parent's owner, added only when
   `owner_url not in existing_ids` (`:2152`) — i.e. only when the owner is *not*
   already in the graph.

3. `emit_fork_parent_stubs(...)` — SoftwareSourceCode stub at
   `src/v2/pipeline/stages/ownership_check.py:2171-2186` (`_stub: True` at `:2184`).
   A minimal fork-parent repo, added only when `target not in existing_ids` (`:2141`).

Pipeline ordering (`src/v2/api.py`): dedup `run_llm_dedup_stage` (`:901`),
`reconcile_entities` (`:929`), `assemble_output` (`:1203`),
`demote_github_props_to_units` (`:1512`), `emit_fork_parent_stubs` (`:1525`),
then `build_jsonld_output` (`:1625`). **All `_stub` writes happen AFTER every
merge/dedup/reconcile stage and right before serialization.**

The `materialize_*` helpers do **not** touch `stub`:
`_materialize_person` (`src/v2/pipeline/stages/refine_with_llm.py:710-796`),
`_materialize_org` (`:799-853`), `_materialize_article` (`:856`),
`_materialize_lab_org` (`:982`) stamp only `_source`/`_discovery_reason`/
`_discovery_confidence` — never `_stub`. Likewise
`_synthesize_owner_person_stub` (`src/v2/pipeline/stages/ownership_check.py:1699`)
and the inferred-owner Person stub in `infer_owners` (`:501-516`) are *called*
"stubs" in prose but do **not** set the `_stub` field.

## Root cause / the gap
There are two distinct issues; the verified evidence points overwhelmingly at (B):

**(A) — claimed by the report, NOT reproduced as written.** "`_stub` set at creation
and never flipped false after a full fetch." This does not occur, because the only
three writers run *after* all enrichment/fetch/merge stages and only build *brand new*
minimal placeholder dicts. There is no later fetch step in the pipeline that would
re-materialize these specific entities, so there is no place a flip-to-false is
"missing" within the current single-pass flow.

**(B) — the real gap: merge contamination + write-only flag.** Two problems:

  1. **The flag is write-only and has no defined semantics.** `_stub` is never read by
     any consumer in this repo, has no test asserting its meaning
     (`grep _stub tests/` finds nothing in `test_ownership_check.py`), and its only
     documentation is a one-line docstring (`ownership_check.py:2108`). A downstream
     RDF consumer receiving `gme-internal:stub` has no contract telling it the flag
     means "reference only, not independently extracted," and — critically — its
     **absence does not mean "fully extracted."** The vast majority of genuinely thin
     references in the graph (inferred-owner Persons `:501`, synthesized author
     Persons `:1699`, discovery-materialized Persons/Orgs) carry **no** `_stub` at all.
     So the flag is neither sound (see #2) nor complete, which is exactly why it
     "can't be used to distinguish thin reference from fully extracted."

  2. **`_stub` can bleed onto a full entity via the generic merge helpers.** Both
     `_merge_into` (`src/v2/pipeline/stages/output_assembly.py:425-432`) and
     `_merge_entity_payload` (`src/v2/pipeline/stages/llm_dedup.py:290-317`) copy any
     key the target is missing, with no exclusion list for internal `_*` provenance
     keys. If a `_stub` entity is ever merged into a fuller same-`id` entity where the
     target lacks `_stub`, the merged (fully-populated) result inherits `_stub: True`.
     In the *current* ordering this is latent (stubs are emitted after the merge
     stages), but `demote_github_props_to_units` and `emit_fork_parent_stubs` mutate
     `assembled.related_entities` in place and any future re-run of an assembly/merge
     pass — or any reordering — would surface exactly the reported symptom. This is
     the concrete mechanism by which "stub appears on a fully-populated entity."

## Proposed fix (semantics + where to clear it)
Recommend **option (b): replace the boolean with an explicit, complete signal**, plus
hardening the merge helpers. Concrete targets:

1. **Define crisp semantics and make the signal complete.** Introduce a single
   `_extraction_level` field (values e.g. `"reference"` vs `"extracted"`) instead of a
   sparse boolean. Stamp `_extraction_level = "reference"` at **all** placeholder
   creation sites so absence is no longer overloaded:
   - `src/v2/pipeline/stages/ownership_check.py:2037-2050` (synthesized github-org unit)
   - `src/v2/pipeline/stages/ownership_check.py:2153-2167` (fork-parent owner Person)
   - `src/v2/pipeline/stages/ownership_check.py:2171-2186` (fork-parent repo)
   - `src/v2/pipeline/stages/ownership_check.py:1699` `_synthesize_owner_person_stub`
   - `src/v2/pipeline/stages/ownership_check.py:501-516` inferred-owner Person stub
   Entities produced by the rule-based agents / reconciliation that *were* fully
   fetched should carry `_extraction_level = "extracted"` (stamp once at
   `reconcile_entities`, `src/v2/api.py:929`, or in the rule-based agent output). Then
   `gme-internal:extraction_level` becomes a sound, complete signal.
   *If a smaller-footprint change is preferred,* keep `_stub` but document it as
   "created as a reference, not independently extracted," stamp it at the five sites
   above, and treat its absence as "extracted" — but this is weaker because nothing
   guarantees full entities lack it (see #2).

2. **Stop merge contamination.** Add an internal-provenance exclusion to the two merge
   helpers so `_stub` / `_extraction_level` is never copied from a placeholder onto a
   richer target:
   - `_merge_into` — `src/v2/pipeline/stages/output_assembly.py:425-432`
   - `_merge_entity_payload` — `src/v2/pipeline/stages/llm_dedup.py:290-317`
   When merging two same-`id` entities, prefer the *richer* one's extraction level
   (`extracted` wins over `reference`) rather than left-bias/first-seen.

3. **Clear-on-materialize (defensive).** If any future enrichment step fully fetches an
   entity that previously existed only as a reference, that step must set
   `_extraction_level = "extracted"` (or `pop("_stub")`). Co-locate this with the
   fetch in the relevant provider/agent so the lifecycle is explicit.

No public-ontology change is required: the field stays `_`-prefixed and is only
surfaced (renamed to `gme-internal:*`) under `include_internal_fields=true`.

## Risks & considerations
- **Public surface:** `_stub` is internal-only. It is stripped by default
  (`_drop_internal_keys`, `jsonld_build.py:130-136`) and only emitted as the
  `gme-internal:stub` IRI when the caller passes `?include_internal_fields=true`
  (`src/v2/api.py:730`, `:1628`; `src/v2/api_models/contracts.py:84`). The
  `gme-internal` namespace is `https://openpulse.science/git-metadata-extractor#`
  (`jsonld_build.py:21`). Renaming `_stub`→`_extraction_level` changes the
  `gme-internal:` term seen by internal-fields consumers — coordinate with any RDF
  consumer that already keys off `gme-internal:stub` (none found in this repo).
- The merge-helper change touches shared dedup/assembly code; guard with the targeted
  exclusion list (only `_stub`/`_extraction_level`, or all `_`-prefixed provenance
  keys) to avoid regressing other internal fields like `_source`.
- Keeping a boolean `_stub` and overloading absence (option a) leaves the signal
  incomplete and is the cheaper but less correct path.

## Test / verification plan
- Unit: extend `tests/v2/test_ownership_check.py` to assert the three placeholder
  sites carry the chosen marker (`_stub`/`_extraction_level="reference"`), and that a
  pre-existing full owner/parent does **not** get a stub added (exercises the
  `existing_ids` guards at `:2141`, `:2152`).
- Regression for contamination: construct two same-`id` entities (one minimal +
  `_stub`, one full, no `_stub`), run through `_merge_into`
  (`output_assembly.py`) and `_merge_entity_payload` (`llm_dedup.py`), assert the
  merged result is `extracted` / has no `_stub`.
- Output: with `include_internal_fields=true`, assert a fully-extracted root repo/person
  node in `@graph` does not carry `gme-internal:stub` and that genuine fork-parent /
  synthesized-unit nodes do carry the reference marker.
- `grep -rn '_stub\|_extraction_level' src/` to confirm every writer is paired with the
  new semantics and there are no stray writes.

## Effort estimate
Low–medium: ~0.5–1 day. Option (a) (document + stamp at 5 sites, treat absence as
extracted) is ~2-3 hrs. Option (b) (rename to `_extraction_level`, stamp the
extracted side, harden two merge helpers, add tests) is ~0.5–1 day. No ontology /
SHACL changes required.

## Open questions
- Are there external RDF consumers (outside this repo) already reading
  `gme-internal:stub`? If so, prefer keeping the term name and only fixing completeness
  + merge contamination (option a + hardening) rather than renaming.
- Is "fully extracted" a binary, or do we want graded levels (`reference` /
  `partial` / `extracted`)? The fork-parent repo stub (`:2171`) carries only
  SHACL-required fields and is clearly a reference; some discovery-materialized persons
  sit in between. A 3-level enum may model reality better than a boolean.
- Should the `demote_github_props_to_units` *reuse* branch (`:2032-2035`), which
  promotes an existing org into a unit, leave that org's existing extraction level
  untouched (current behavior) or assert it stays `extracted`? Confirm intended.
