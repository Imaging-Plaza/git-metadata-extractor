# Conversion test — real data, every platform, against the raw profile

The assessment estimated; this measures. Real extraction data from **every
source GME reads**, converted into the proposed raw profile and validated with
pySHACL against the actual shapes from PR #25.

**38 violations as published → 0 with the proposed changes applied.**

Run 2026-08-07. pySHACL 0.28.1, rdflib 6.3.2. 508 triples.

---

## Input — real, not invented

Two kinds of source, both carrying genuine provenance:

- **Committed snapshots** — `tests/v2/fixtures/providers/live_snapshots/`
  (the `gimie-baseline` dataset): real GitHub, ROR, ORCID and Infoscience
  responses for `sdsc-ordes/gimie`, captured **2026-02-24T14:18:28Z**, each
  with a `.meta.json` sidecar holding the request URL and capture time.
- **Live captures** — [`fetch_live_sources.py`](fetch_live_sources.py) pulls
  deps.dev, ecosyste.ms, OpenAlex, HuggingFace and Docker Hub into
  `examples/sources/`, recording each request URL and fetch time in the same
  shape, so the test stays reproducible offline afterwards.

Every literal traces to a captured payload, and every `pulse:retrievedFrom` /
`pulse:retrievedAt` is a real request URL and timestamp — the §18 provenance
model is exercised on genuine data, not stubs.

Generators:
[`build_instance_example.py`](build_instance_example.py) (GitHub + ROR) then
[`build_instance_multisource.py`](build_instance_multisource.py) (everything
else) → [`examples/gimie-raw-instance.ttl`](examples/gimie-raw-instance.ttl).

### Platforms exercised

| Platform | Source | What it contributes |
|---|---|---|
| **GitHub** | snapshot | repository, 10 contributors, the org, languages |
| **ROR** | snapshot | the organization authority record |
| **ORCID** | snapshot | a person, a *second* `PlatformProfile` for the same human, employments → `Membership`, external identifiers, keywords, biography |
| **Infoscience** | snapshot | scholarly records |
| **deps.dev** | live API | `pkg:pypi/gimie@0.4.0` and 6 resolved dependencies with `isDirectDependency` / `resolvedVersion` |
| **ecosyste.ms** | live API | repository purl, `developmentDistributionScore` |
| **OpenAlex** | live API | the same organization from a *third* source, acronym, `ExternalIdentifier` |
| **HuggingFace** | live API | a model repository typed as `schema:SoftwareSourceCode` |
| **Zenodo** | live rete query | a real deposit and **how** it relates (`isSupplementTo`) |
| **Docker Hub** | live API | **nothing** — the namespace probed returned zero results, so no image instance exists in the graph |

| Node type | n |
|---|---|
| `schema:Person` (provisional) | 11 |
| `pulse:PlatformProfile` | 11 |
| `pulse:Contribution` | 10 |
| `pulse:ExtractionOutput` | 7 |
| `pulse:Package` | 7 |
| `pulse:Observation` | 6 |
| `pulse:DependencyRelation` | 6 |
| `pulse:UnmappedField` | 4 |
| `schema:SoftwareSourceCode` | 3 |
| `pulse:GitIdentity` | 3 |
| `pulse:ExternalIdentifier` | 3 |
| `schema:ScholarlyArticle` | 3 |
| `org:Organization`, `org:Membership` | 2 each |
| `pulse:ExtractionRun`, `prov:SoftwareAgent` | 1 each |

---

## Result

| Shapes | Conforms | Violations |
|---|---|---|
| PR #25 `ontology-shapes-raw.ttl` **as published** + our patch | ❌ | **38** |
| …with the proposed changes applied | ✅ | **0** |

### The 38, and which ask each one is

| n | Path | Cause | Ask |
|---|---|---|---|
| 14 | `pulse:partOfRun` | profiles, memberships, packages and images are closed **and** lack `partOfRun` | **#14**, #13 |
| 6 | `pulse:hasDependency` | closed `RawRepositoryShape` | #16-terms, #13 |
| 4 | `pulse:hasUnmappedField` | closed shape rejects preserved-but-unmodelled fields | **#17**, #13 |
| 2 | `pulse:platform` | `pulse:ROR` and `pulse:OpenAlex` are not `PlatformEnumeration` members | **#4** |
| 1 each | `hasProjects`, `hasIssues`, `sizeInBytes`, `forkNetworkCount`, `primaryProgrammingLanguage`, `distributedAs`, `hasDeposit`, `depositRelation`, `accessRights` | closed shape | #7, #8, #13, #19 |
| 1 | `pulse:identifierScheme` | `pulse:OpenAlexScheme` is not an `IdentifierSchemeEnumeration` member | **#5** |
| 1 | `pulse:partOfRun` | **"More than 1 values"** — see the new finding below | **#22** |

**The closed shapes remain the dominant failure mode**: 33 of 38 are a closed
shape rejecting a value, not a modelling disagreement. Ask #13 alone clears
them.

### New finding, which only multi-source data could surface

`pulse:partOfRun` is **`sh:maxCount 1`** in the published shapes. So an entity
described by two platforms cannot record both — and it fired immediately, on
`https://ror.org/02s376052`, which **ROR and OpenAlex both describe**.

For a profile whose stated job is accepting everything from every source, an
entity being attributable to exactly one extraction output is a real limit. The
same organization, repository or person seen by three sources is the normal
case, not the exception. **`partOfRun` should be unbounded** — filed as ask
#22.

This is precisely the class of problem a single-source test cannot find, which
is the argument for having run this one.

### Applying the asks

Simulated programmatically rather than by hand-editing their file: flip
`sh:closed` to `false` on the closed raw shapes, drop `sh:maxCount` from
`partOfRun` and add it to the shapes lacking it, and type the proposed
enumeration members (`ROR`, `OpenAlex`, `OpenAlexScheme`, `PyPI`,
`RuntimeDependency`, `SupplementTo`). Then **0 violations**.

The asks are therefore not just *necessary* but *sufficient* for real data from
every platform we read.

## Three bugs of mine that the validator caught

Recorded because a proposal that reports non-existent problems is worth less
than one that reports none.

1. **A false finding I nearly filed.** The first run reported
   `schema:license: Value is not of Node Kind sh:IRI`, which looked real —
   GitHub returns `"Apache-2.0"` while the shape demands an IRI. It was my
   generator: GME already emits `https://spdx.org/licenses/{id}.html` via
   `repository_agent.py::_normalize_license_url()`, and the strict schema
   documents the field as *"SPDX license IRI"*.
2. **The ORCID id.** `person["path"]` is `/0000-0002-1825-0097/person`;
   stripping slashes left `0000-0002-1825-0097/person`, which failed the ORCID
   `sh:pattern`. Now takes the first path segment — and note the shape caught
   it, which is the pattern doing its job.
3. **Literal escaping.** The ORCID biography contains `\r\n`, producing
   *"newline found in string literal"*. `esc()` now collapses all whitespace
   rather than only `\n`.

## Caveats

- **Still not the whole profile.** `pulse:Funding`, `pulse:Collection`,
  `pulse:Community`, `pulse:Advisory` and `schema:Project` are not exercised —
  no funding or advisory data was captured for this repository. 38 is a floor,
  not a total.
- **Docker Hub contributed nothing.** The namespace probed
  (`hub.docker.com/v2/repositories/sdscordes/`) returned zero results. The
  project publishes to GHCR instead, so the image layer is untested.
- **`pulse:GitIdentity` uses platform noreply addresses**
  (`{id}+{login}@users.noreply.github.com`), because the contributors API
  returns logins, not commit identities. Hashing and `isPseudonymousEmail` are
  exercised; the multi-identity case that motivates §9 is not — that needs the
  git-log path in [`git-author-identities.md`](git-author-identities.md).
- **Enumeration members must be loaded as data.** `pulse:GitHub` and friends
  live in `ontology-enumerations-canonical.ttl`; without that file in the data
  graph every `sh:class …Enumeration` check fails. Expected SHACL behaviour,
  not a finding — but it caught me on the first run.

## Reproducing

```bash
python dev/v4.0.0/fetch_live_sources.py          # refresh the live captures
python dev/v4.0.0/build_instance_example.py      # GitHub + ROR, from snapshots
python dev/v4.0.0/build_instance_multisource.py  # every other platform

pyshacl -s <(cat …/ontology-shapes-raw.ttl dev/v4.0.0/ontology-shapes-raw.additions.ttl) \
        -e …/ontology-enumerations-canonical.ttl \
        dev/v4.0.0/examples/gimie-raw-instance.ttl
```
