# Can the raw profile hold everything we read?

Every source GME reads, against what the raw profile can express. The raw
profile is the extractor's profile, so the target is simple: **anything we
observe must have somewhere to go, or we drop it.** Today we drop a lot into
`gme-internal:`.

Verified 2026-07-31 against PR #25 (`feature/platform-profiles`), GME 3.0.0,
and the rete ontologies in [`../rete/`](../rete/).

Legend: **✓** covered by PR #25 · **+** proposed in
[`ontology.ttl`](ontology.ttl) · **✗** no home anywhere yet.

---

## The method to copy

`../rete/epfl-infoscience.ttl` states the rule we should follow verbatim:

> REUSES the standard vocabularies those fields map to … and mints `infs:`
> terms ONLY for the EPFL/CRIS-specific fields with no standard equivalent.
> Every `infs:` term is `rdfs:subPropertyOf` the exact standard the DSpace
> field corresponds to.

So for each source below: reuse the standard, mint only the residue, and always
subproperty it to the standard it corresponds to. §13 of `ontology.ttl` already
does that for `cito:cites`, `dct:subject`, `frapo:Grant`.

---

## 1. GitHub — the primary source

| What we read | Raw profile |
|---|---|
| repo core: name, description, license, language, stars, forks, created | ✓ (`repositoryStars`/`Forks`/`Handle` after the rename) |
| topics, visibility, archived, default branch, watchers, open issues, wiki/pages/discussions, releases, tags, pushed date | ✓ |
| README, FUNDING.yml, publiccode.yml, SECURITY.md, CODE_OF_CONDUCT.md | ✓ |
| dependency counts + dependents | ✓ counts; **+** `dependsOnPackage` (SBOM is package-shaped) |
| CONTRIBUTING, docs URL, CI presence, coverage, community health, issue/PR templates, badges | **+** §3 |
| size in bytes, fork network count, archived date, primary language | **+** §4 |
| compose files / images, container images, Docker Hub | **+** §2 |
| account profile: login, id, node id, bio, company, location, avatar, followers, gists, profile README | ✓ on `PlatformProfile`; **+** `profileReadme` |
| **commit author/committer names + emails** | **+** §9 `GitIdentity` — blocked by `maxCount 1` today |

## 2. HuggingFace

| What we read | Raw profile |
|---|---|
| model/dataset/space kinds, likes, downloads, pipeline tag, task, library, framework, language, gated, modality, splits, config, SDK, hardware, runtime status, file/LFS/Xet counts | ✓ — PR #25 covers this well |
| base-model lineage | ✓ `externalReference` / `relatedRepository` |
| collections | ✓ `pulse:Collection` (**+** aligned `⊑ skos:Collection`) |
| org/user profiles | ✓ `PlatformProfile` / `OrganizationProfile` |

The best-covered source in PR #25. Our gap is wiring, not vocabulary — the
`hf_*` indices already hold most of it.

## 3. Zenodo

| What we read | Raw profile |
|---|---|
| record, DOI, concept DOI, version, files, communities | ✓ (`conceptDoi`, `version`, `fileCount`, `memberOfCommunity`, `Community`) |
| creators/contributors | ✓ |
| **how a deposit relates to the repo** (`isDerivedFrom`/`isPartOf`/`isSupplementTo`) | **+** §13 `depositRelation` — observed in `zenodo-records` |
| **access status** (`info:eu-repo/semantics/openAccess`) | **+** §13 `accessRights` |
| keywords | ✓ `keyword` (**+** aligned `⊑ dct:subject`) |

## 4. ORCID — do we have all of it? No, and in two different ways

GME's provider (`providers/orcid_provider.py`) fetches exactly **three**
endpoints against `pub.orcid.org/v3.0`: `/person`, `/employments`,
`/educations`. So there are two separate gaps — terms we lack, and data we
never ask for.

| What we read today | Raw profile |
|---|---|
| ORCID iD, biography, country, keywords, other names, researcher URLs, emails, external identifiers (all from `/person`) | ✓ / **+** (`biography`, `country`, `keyword`, `socialLink`, `ExternalIdentifier`; name variants → `skos:altLabel`, ask #10) |
| `/employments`, `/educations` (+ role, dates, department, degree) | ✓ `membershipType`, `department`; **+** `qualification` |

### Terms missing: four of ORCID's seven affiliation types

ORCID models seven affiliation kinds, each its own endpoint —
`/employments`, `/educations`, `/qualifications`, `/invited-positions`,
`/distinctions`, `/memberships`, `/services`. `MembershipTypeEnumeration` has
three. **+** §17 adds `Qualification`, `InvitedPosition`, `Distinction`,
`Service`.

Without them a qualification, an invited chair, a prize and committee service
all collapse into "membership" — erasing the difference between *held a
position at*, *was recognised by* and *sat on a committee of*.

### Data we never fetch

| ORCID endpoint | Raw profile | Why it matters |
|---|---|---|
| `/fundings` | ✓ term exists (`pulse:Funding`, `hasFunding`) — **we just don't call it** | PR #25 added the class *for* ORCID funding; we would populate nothing |
| `/qualifications`, `/invited-positions`, `/distinctions`, `/memberships`, `/services` | **+** §17 once added | five of seven affiliation endpoints unfetched |
| `/works` | ✓ ≈ Article | we discover articles via Infoscience instead, so ORCID's own work list is unused |
| `/peer-reviews` | ✗ **not modelled, deliberately** | would need its own class; we do not fetch it, so modelling it would be ahead of the requirement |
| `/research-resources` | ✗ **not modelled, deliberately** | same |

So: the honest answer to "do we have all ORCID properties" is that we have most
of `/person` and two of seven affiliation types, the terms for a third
(funding) without the fetch, and nothing for peer review or research
resources. The cheapest real win is `/fundings` — the term already exists in
PR #25.

## 5. ROR

| What we read | Raw profile |
|---|---|
| ROR id as org `@id` | ✓ |
| name, aliases, acronyms, country, types, status, established year, links, parent/child | **+** §5 `establishedYear`, `registryStatus`, `acronym`; ✗ **ROR is not a `pulse:PlatformEnumeration` member** (ask #4) |

ROR is our organization authority and the wider ecosystem's — `../rete/scholar.ttl`
makes it *the* authority — yet it cannot be named as a source platform.

## 6. EPFL Infoscience — the new ontology changes the picture

`../rete/epfl-infoscience.ttl` (v1.2.0) models DSpace-CRIS properly, and it
exposes entity kinds and identifiers that neither PR #25 nor our proposal has:

| Infoscience term | Raw profile |
|---|---|
| `infs:Publication`, `infs:Product` | ✓ ≈ `schema:ScholarlyArticle` |
| **`infs:Thesis`** (+ `doctoralSchool`, `faculty`, `institute`, `thesisNumber`, `jury`, `publicDefenseYear`) | ✗ no thesis type at all |
| **`infs:Patent`** (+ number, country, kind code, date) | ✗ |
| **`infs:Journal`** (+ `issn`, `eissn`) — the venue | ✗ no venue/serial concept |
| **`infs:Event`** (+ place, dates, acronym) — the conference | ✗ |
| **`infs:hasAdvisor`** — thesis supervision | ✗ no person→person relation |
| **`infs:sciper`** — EPFL person id, "join key to the epfl-graph dataset" | ✗ not in `IdentifierSchemeEnumeration` (we asked only for Infoscience) |
| `infs:unitCode`, `unitInfoscienceCode`, `acronym`, `orgUnitLevel`, `orgUnitActive`, `foundingDate`, `director`, `parentOrg` | **+** §5 covers most; ✗ `orgUnitLevel` (lab vs institute vs school) |
| `infs:scopusAuthorId`, `researcherId`, `openalexAuthorId` | ✓ `ExternalIdentifier`; **+** OpenAlex scheme (ask #5) |
| `infs:nameVariant` (`crisrp.name.variant`) | ask #10 (`skos:altLabel`) — Infoscience mints its own, so the need is real |
| `infs:affiliationName/Role/StartDate/EndDate` | ✓ Membership |
| `infs:abstract`, `fullText`, `handle`, `uuid`, `rights` | ✗ (`abstract` especially — we read it and drop it) |

**Its class hierarchy is also a free alignment for us**: `infs:OrgUnit ⊑
org:OrganizationalUnit`, `infs:Journal ⊑ fabio:Journal`, `infs:Thesis ⊑
schema:Thesis, fabio:Thesis`. If we adopt venue/thesis/patent types, subclass
them the same way rather than inventing a parallel hierarchy.

## 7. The rest of our read surface

| Source | Raw profile |
|---|---|
| **OpenAlex** (works, authors, institutions, sources, topics, concepts) | ✗ not a platform enum member (ask #4); `openalexAuthorId` → `ExternalIdentifier` |
| **ETHZ Research Collection** (DSpace, Infoscience's sister) | ✗ not a platform member; the `infs:` pattern would transfer directly |
| **SNSF** grants | ✓ `pulse:Funding`; ✗ not a platform member |
| **SWISSUbase** (studies, datasets, persons, institutions) | ✗ nothing; "study" has no type |
| **RenkuLab** (projects, groups, users, data connectors) | ✗ nothing; a Renku *project* is neither repo nor article |
| **DockerHub** | **+** §2 `ContainerImage`; ✗ not a platform member |
| **EPFL Graph** disciplines | ✓ `pulse:discipline`; `infs:sciper` is the documented join key |
| **package registries** (PyPI/npm/Maven/Conda/…) | **+** §1 `pulse:Package` |
| **GH Archive** | ✗ not published as a graph; would bulk-populate `GitIdentity` |

---

## What "putting it together" actually requires

Three additions beyond the current proposal, all justified by a source we
already read:

1. **Scholarly output kinds we ingest but cannot type** — Thesis, Patent, and
   the venue (`Journal`/serial with ISSN) plus Event/conference. Infoscience
   returns these today; our article agent queries Infoscience today; so we
   receive records we cannot model. Cheapest form: add `Thesis` and `Patent` to
   `PublicationTypeEnumeration`, and reuse `fabio:Journal` / `schema:Periodical`
   for the venue rather than minting one.
2. **Person identifiers and relations**: EPFL **sciper** as an
   `IdentifierSchemeEnumeration` member (it is the join key to EPFL Graph), and
   `hasAdvisor` for supervision — a person→person edge nothing in `pulse:`
   currently expresses, and the strongest signal of research lineage we can
   get.
3. **Platform members for the read surface we actually have**: ROR, DockerHub,
   OpenAlex, ETHZ RC, SNSF, SWISSUbase, RenkuLab, EPFL Graph (ask #4) — without
   them, `pulse:platform` cannot name where half our data came from, and
   `partOfRun` / `ExtractionOutput` cannot partition by it either.

Deliberately *not* proposed: types for SWISSUbase studies and RenkuLab
projects. We read those indices for search, not to emit entities, so inventing
classes for them would be modelling ahead of the requirement — the opposite of
what the Infoscience ontology does well.
