# Conversion test — real data against the raw profile

The assessment estimated; this measures. A real GME extraction converted into
the proposed raw profile and validated with pySHACL against the actual shapes
from PR #25.

**Result: 21 violations as published, 0 with the proposed changes applied.**

Run 2026-08-07. pySHACL 0.28.1, rdflib 6.3.2.

---

## Input — real, not invented

`tests/v2/fixtures/providers/live_snapshots/` (the `gimie-baseline` dataset):
real GitHub, ROR, ORCID and Infoscience API responses for
`sdsc-ordes/gimie`, captured **2026-02-24T14:18:28Z**, each with a
`.meta.json` sidecar carrying the request URL and capture time.

Every literal in the instance graph traces to a snapshot field, and every
`pulse:retrievedFrom` / `pulse:retrievedAt` is the actual request URL and
capture timestamp — the provenance is real, not stubbed.

Generator: [`build_instance_example.py`](build_instance_example.py) →
[`examples/gimie-raw-instance.ttl`](examples/gimie-raw-instance.ttl)
(337 triples).

| Node type | n | From |
|---|---|---|
| `schema:Person` (provisional) | 10 | GitHub contributors |
| `pulse:PlatformProfile` | 10 | one per contributor |
| `pulse:Contribution` | 10 | with real commit counts (cmdoret 128, sabinem 40, …) |
| `pulse:UnmappedField` | 4 | real GitHub fields with no term |
| `pulse:Observation` | 4 | real request URLs + capture times |
| `pulse:GitIdentity` | 3 | hashed local part + domain |
| `org:Organization` | 2 | GitHub org + ROR record |
| `pulse:ExtractionOutput` | 2 | one per platform |
| `schema:SoftwareSourceCode` | 1 | the repository |

## Result

| Shapes | Conforms | Violations |
|---|---|---|
| PR #25 `ontology-shapes-raw.ttl` **as published** + our patch | ❌ | **21** |
| …with asks #13, #14, #4 applied | ✅ | **0** |

### The 21, and which ask each one is

| n | Path | Cause | Ask |
|---|---|---|---|
| 11 | `pulse:partOfRun` | `PlatformProfile` ×10 and `OrganizationProfile` ×1 are closed **and** lack `partOfRun` — the nodes that exist to hold one platform's data cannot say which run produced them | **#14**, #13 |
| 4 | `pulse:hasUnmappedField` | `RawRepositoryShape` is closed, so preserved-but-unmodelled fields are rejected | **#17**, #13 |
| 1 | `pulse:sizeInBytes` | closed shape | #8, #13 |
| 1 | `pulse:forkNetworkCount` | closed shape | #8, #13 |
| 1 | `pulse:primaryProgrammingLanguage` | closed shape | #8, #13 |
| 1 | `pulse:hasIssues` | closed shape | #7, #13 |
| 1 | `pulse:hasProjects` | closed shape | #7, #13 |
| 1 | `pulse:platform` | `pulse:ROR` is not a `PlatformEnumeration` member, so a ROR-sourced organization cannot state where it came from | **#4** |

Two things this shows that the prose could not:

- **The closed shapes are the dominant failure mode.** 20 of 21 violations are
  a closed shape rejecting a value, not a modelling disagreement. Ask #13 alone
  clears them.
- **Ask #4 is not cosmetic.** The single non-closed-shape violation is ROR
  missing from the platform enumeration — hit immediately, on the first real
  organization, because ROR is our organization authority.

### Applying the asks

Simulated programmatically rather than by hand-editing their file: flip
`sh:closed` to `false` on the 12 closed raw shapes, add a `pulse:partOfRun`
property shape to the 8 node shapes lacking one, and type `pulse:ROR` as a
`PlatformEnumeration` member. Then **0 violations**.

That is the useful half of the result: the asks are not just *necessary*, they
are *sufficient* for real data from our largest source.

## A false finding I nearly filed

The first run produced a 22nd violation —
`schema:license: Value is not of Node Kind sh:IRI` — and it looked like a real
gap: GitHub returns an SPDX id string (`"Apache-2.0"`) while the shape demands
an IRI.

It was my generator's bug. GME already emits an IRI:
`repository_agent.py::_normalize_license_url()` produces
`https://spdx.org/licenses/{spdx_id}.html`, and the strict schema documents the
field as *"SPDX license IRI"*. The instance builder was emitting the raw
`spdx_id` instead. Fixed, and recorded here because a proposal that reports
non-existent problems is worth less than one that reports none.

## Caveats

- **Not the whole profile.** Articles, Memberships, Funding, Packages and
  Collections are not exercised — the snapshot set has no Zenodo or deps.dev
  capture, and ORCID/Infoscience snapshots exist but were not wired in. The 21
  violations are a floor, not a total.
- **`pulse:GitIdentity` uses platform noreply addresses** (`{id}+{login}@users.
  noreply.github.com`), because the contributors API returns logins, not commit
  identities. Real addresses need the git-log path in
  [`git-author-identities.md`](git-author-identities.md). The hashing and the
  `isPseudonymousEmail` flag are exercised; the multi-identity case is not.
- **Enumeration members must be loaded as data.** `pulse:GitHub` and friends
  are declared in `ontology-enumerations-canonical.ttl`; without that file in
  the data graph, every `sh:class …Enumeration` check fails. That is expected
  SHACL behaviour, not a finding — but it caught me on the first run and will
  catch anyone else.

## Reproducing

```bash
python dev/v4.0.0/build_instance_example.py     # regenerate from the snapshots
pyshacl -s <(cat …/ontology-shapes-raw.ttl dev/v4.0.0/ontology-shapes-raw.additions.ttl) \
        -e …/ontology-enumerations-canonical.ttl \
        dev/v4.0.0/examples/gimie-raw-instance.ttl
```
