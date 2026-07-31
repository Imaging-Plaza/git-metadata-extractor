# v4.0.0 — what we are proposing to the ontology

Index of every change git-metadata-extractor wants in
[`sdsc-ordes/open-pulse-ontology`](https://github.com/sdsc-ordes/open-pulse-ontology),
assessed against PR #25 (`feature/platform-profiles`).

**Nothing here is implemented in GME and nothing has been sent yet.** These are
proposals plus the evidence for them.

Background: the feasibility assessment in
[`../ontology-v3-profiles/`](../ontology-v3-profiles/README.md) — read that
first if you want the "how hard is adapting GME" answer rather than the list of
asks.

## Files in this directory

| File | What it is |
|---|---|
| [`ontology.ttl`](ontology.ttl) | The term proposal — 61 `pulse:` terms (4 classes, 41 properties, 3 shapes). Every term carries `gme:mapsFrom`, naming the GME internal field it would promote. |
| [`ontology-shapes-raw.additions.ttl`](ontology-shapes-raw.additions.ttl) | Drop-in patch for **their** `ontology-shapes-raw.ttl`: adds the missing `RawContributionShape` and a `RawGitIdentityShape`. |
| [`git-author-identities.md`](git-author-identities.md) | Why git identities need a node, how we obtain all of them, six caveats, and the settled email policy. |
| [`examples/`](examples/) | Two instance graphs proving the cardinality problem is real — one conforms with the patch, one fails without it. |

---

## The asks, and where each one lands in their layout

Their profile is split across six files, so each ask names the file it belongs
in. Priority: **P0** = blocks a term already in PR #25 or a requirement we
have; **P1** = strands extraction we do today; **P2** = worth having.

| # | Ask | Their file(s) | Spec'd in | Pri |
|---|---|---|---|---|
| 1 | **`pulse:Package`** + ecosystem / version / registry / purl identifier, `distributedAs`, `dependsOnPackage` — or widen `pulse:dependsOn`'s range | `ontology-definitions-raw.ttl`, `ontology-shapes-raw.ttl` | [`ontology.ttl`](ontology.ttl) §1 | **P0** |
| 2 | **`pulse:GitIdentity`** + unbounded `hasGitIdentity`; and the missing **`RawContributionShape`** | `ontology-definitions-raw.ttl`, `ontology-shapes-raw.ttl` | [patch](ontology-shapes-raw.additions.ttl), [`ontology.ttl`](ontology.ttl) §9 | **P0** |
| 3 | Lift `sh:maxCount 1` on `gitAuthorName` / `gitAuthorEmail` (or replace with #2's link) | `ontology-shapes-canonical.ttl` | [patch](ontology-shapes-raw.additions.ttl) tail | **P0** |
| 4 | `PlatformEnumeration` members: **ROR**, DockerHub, OpenAlex, ETHZ Research Collection, SNSF, SWISSUbase, RenkuLab, EPFL Graph | `ontology-enumerations-canonical.ttl` | [`ontology.ttl`](ontology.ttl) §8 | **P1** |
| 5 | `IdentifierSchemeEnumeration`: **Infoscience** (+ OpenAlex, ROR) | `ontology-enumerations-raw.ttl` | [`ontology.ttl`](ontology.ttl) §8 | **P1** |
| 6 | **`pulse:ContainerImage`**, `publishesImage` / `referencesImage`, `composeManifest` | `ontology-definitions-raw.ttl`, `ontology-shapes-raw.ttl` | [`ontology.ttl`](ontology.ttl) §2 | **P1** |
| 7 | Repository health: CI, coverage, community health, badges, issue/PR templates, contributing guide, docs URL | `ontology-definitions-raw.ttl`, `ontology-shapes-raw.ttl` | [`ontology.ttl`](ontology.ttl) §3 | **P1** |
| 8 | Repository facts: `sizeInBytes`, `forkNetworkCount`, `archivedDate`, `primaryProgrammingLanguage`, `profileReadme` | `ontology-definitions-raw.ttl`, `ontology-shapes-raw.ttl` | [`ontology.ttl`](ontology.ttl) §4 | **P2** |
| 9 | Registry org metadata: `establishedYear`, `registryStatus`, `acronym`, `unitCode` | `ontology-definitions-raw.ttl`, `ontology-shapes-raw.ttl` (+ canonical `OrganizationShape`) | [`ontology.ttl`](ontology.ttl) §5 | **P2** |
| 10 | Permit **`skos:altLabel`** on Person / Organization / Repository — no new term, the closed shapes just exclude it | `ontology-shapes-raw.ttl`, `ontology-shapes-canonical.ttl` | [`ontology.ttl`](ontology.ttl) §6 | **P2** |
| 11 | `PackageEcosystemEnumeration` (purl types) | `ontology-enumerations-raw.ttl` | [`ontology.ttl`](ontology.ttl) §7 | with #1 |
| 12 | **`pulse:samePersonAs` should not be `⊑ owl:sameAs`** — prefer `skos:exactMatch`, or standalone with a comment that it is a unifier assertion rather than an identity axiom | `ontology-definitions-provenance.ttl` | [`../rete/gme-alignment-findings.md`](../rete/gme-alignment-findings.md#a-finding-worth-acting-on-samepersonas-should-not-be-a-sameas) | **P1** |

Most of it is the **raw** profile, which is the expected answer: raw is the
extractor's profile, and everything above is data an extractor observes. Only
#3, #4 and parts of #9/#10 touch canonical.

### Three of these are time-sensitive

Cheap while PR #25 is open, expensive after it merges:

- **#1** — `pulse:dependsOn` is already in the PR and we cannot populate it
  (SBOM data is package-shaped, the edge is repository-shaped).
- **#3** — the `maxCount 1` cap blocks "all author names and emails" outright.
- **#4/#5** — pure enumeration members: no new classes, no shape edits.

---

## What we are NOT asking for

Recorded so the proposal is read as considered rather than maximal:

- **No new term for name variants.** `skos:altLabel` already covers
  `_aliases` / `_original_name` / `_orcid_other_names`, and both profiles
  import SKOS. Ask #10 is only that the shapes permit it.
- **No change to `pulse:gitAuthorEmail` semantics.** We never populate it —
  hashed local part plus domain only. It stays in the shapes for extractors
  with a different policy.
- **Nothing for pipeline bookkeeping.** `_person_ref`, `_stub`, `_embedded`,
  `_source_index`, `_score` and friends should keep being stripped; they are
  our internals, not ontology material. Inventory in
  [`../ontology-v3-profiles/field-mapping.md`](../ontology-v3-profiles/field-mapping.md#unmapped--gaps-to-feed-back).
- **Nothing on the provenance profile yet** beyond ask #12. It needs RDF-star,
  which our pinned stack (`rdflib==6.3.2`, `pyshacl==0.28.1`) cannot emit or
  validate — and their own file header notes rdflib 7.6.0 cannot parse
  Turtle-star either. That is a tooling conversation, not a term request. Ask
  #12 is the exception because it is a one-word semantic fix with a real
  consequence, and needs no RDF-star to matter.

## External corroboration

The rete scholarly-graph alignment ontology (`dev/rete/scholar.ttl`, v1.4.0)
independently arrives at GME's exact identifier conventions — DOI, ORCID and ROR
IRIs identical — which means our output already joins ten scholarly graphs with
no `owl:sameAs`. It also treats **ROR as *the* organization authority**, which
is direct outside support for ask #4, and its explicit "use `skos:exactMatch`,
not `owl:sameAs`" rule is where ask #12 comes from. Survey:
[`../rete/gme-alignment-findings.md`](../rete/gme-alignment-findings.md).

---

## Reproducing the evidence

Both example graphs and the merge check, with `pyshacl` and `rdflib` from our
dev extra:

```bash
# the patch merges into their raw shapes without collisions
python - <<'PY'
from rdflib import Graph
g = Graph()
g.parse("path/to/open-pulse-ontology/src/ontology/ontology-shapes-raw.ttl", format="turtle")
g.parse("dev/v4.0.0/ontology-shapes-raw.additions.ttl", format="turtle")
print(len(g), "triples merged")
PY

# three identities on one contribution: conforms WITH the patch
pyshacl -s <(cat path/to/ontology-shapes-raw.ttl dev/v4.0.0/ontology-shapes-raw.additions.ttl) \
        dev/v4.0.0/examples/multi-identity-contribution.ttl

# the same data the only way canonical allows: fails on both maxCount 1
pyshacl -s path/to/ontology-shapes-canonical.ttl \
        dev/v4.0.0/examples/multi-identity-under-current-canonical.ttl
```

Results recorded 2026-07-31, pySHACL 0.28.1: merged shapes 988 triples, no
shape-name collision, no duplicate `sh:targetClass`; first example conforms;
second fails with `More than 1 values on <contrib1>->pulse:gitAuthorName` and
the same for `gitAuthorEmail`.

---

## Status

| Step | State |
|---|---|
| Assess raw + provenance against GME 3.0.0 | done — [`../ontology-v3-profiles/`](../ontology-v3-profiles/README.md) |
| Term proposal drafted + parse-validated | done — 419 triples |
| Raw shapes patch drafted + SHACL-validated | done |
| Sent to the ontology team | **not yet** |
| Implemented in GME | **not started** — waits on which asks are accepted |

Nothing in GME changes until the ontology answers, with one exception worth
starting regardless: `pulse:samePersonAs` is a plain triple needing no new
term, and we currently compute that knowledge in `llm_dedup` and then discard
it by merging IDs.
