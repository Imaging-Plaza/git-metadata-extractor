# Enrichment quality fixes

**Goal:** Correct the four defects demonstrated by the 2026-11-13 live benchmark.
**Architecture:** Preserve the existing source-service boundary and v3 canonical
contract. Prefer deterministic evidence and validated producer payloads; never
invent a DOI, assert ownership from retrieval similarity alone, or hide degraded
coverage behind a successful empty lookup.
**Execution:** Use the existing clean `feat/2026-11-refactor` checkout, as required
by the repository and the user's local-branch preference. No publication.

## Tasks

- [x] ROR identity/ownership: reproduce ANTsX versus Normalization Housing
  Foundation; locate the earliest incorrect acceptance in organization matching
  and parent inference. Gate acceptance on corroborating identity evidence and
  preserve legitimate institutional matches and GitHub ownership. Test ambiguous
  generic names, conflicting websites, and known positive matches.
- [x] Organization retention: reproduce a valid ROR organization failing strict
  `anyOf`; align its producer/normalization contract with accepted identifiers.
  Preserve meaningful rejection of invalid candidates. Run mapping/schema/model
  checks where touched; coordinate generated model updates centrally.
- [x] Explicit citations: consume already fetched CITATION.cff, CITATION.bib and
  explicitly requested README citations before optional publication search.
  Test the seven benchmark cases and irrelevant references/software archive DOIs.
  Preserve attribution and valid article/citation representation for arXiv-only
  references without fabricated DOI or ontology fields.
- [x] Contributor coverage: reproduce unavailable GIMIE contributors, use a
  bounded supported fallback where possible, and expose partial/unavailable
  coverage in response warnings/run evidence. Distinguish a truly empty repository
  from failed or truncated retrieval. No fabricated contributor counts.
- [x] Integrate, independently review, run meaningful deterministic regression
  suites and required mapping/schema generation checks, then rerun affected live
  cases against the pinned local sources service with no index/store writes.
- [x] Record before/after outcomes and remaining limitations; commit locally.

The benchmark report and retained `data/benchmarks/sources-20261113` artifacts
are the evidence baseline. Keep that baseline unchanged. Production fixes and
new verification artifacts belong to a separate follow-up run.

## Validation environment

Docker image `gme-sources-lock-validation:20261112`, mount this checkout at
`/work/gme`, set `PYTHONPATH=/work/gme`, use Python entrypoint. Native Python lacks
the URI-format validation extras; Docker provides the tested dependencies.
Independent tasks own separate files and tests; root coordinates integration,
generated schemas, live reruns and documentation. No agent commits independently.

## Review findings incorporated

- Skip pipeline caching for unavailable/partial contributor coverage; version
  the key to bypass old silent minimal responses.
- Preserve citation links before HTML cleanup. Respect balanced DOI parentheses,
  BibTeX comment boundaries, neighboring references, and arXiv eprint syntax.
  Normalize arXiv search seeds while retaining full output IRIs.
- Preserve late hybrid ROR identifiers through raw/canonical projection without
  rewriting UUIDs or inbound references.
- Restore query-log test ContextVars after each test; full-suite execution exposed
  an existing cross-module fixture leak.

Separate follow-up: late-created memberships need the inverse Person linkage
used by source routing. Baseline artifacts already exhibit this defect; no
source attribution is invented in this fix. See the reviewed follow-up report.

## Final verification

- Full offline V2 suite: 2,006 passed, 2 skipped, 3 live-credential cases excluded.
- Eleven selected live results: 44/44 targeted facts, 11/11 canonical and 11/11
  per-named-graph raw conformance checks. Seven primary citations retained.
- ANTs rule-based/hybrid ownership corrected; final hybrid Iowa ROR survives.
- Linux still receives HTTP 403; explicit unavailable/partial evidence retained.
- 23 mappings + 3 policies validated; strict documentation build passed.
- Original benchmark preserved; 13 follow-up attempts retained (including two
  debugging attempts superseded by explicit reruns). Sources checkout unchanged.
- Remaining 70 stranded Membership nodes are the preexisting inverse-link
  follow-up, not treated as a successful routing check merely because SHACL passes.
