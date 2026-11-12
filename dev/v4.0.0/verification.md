# Verification — every external IRI the proposal asserts

[`ontology.ttl`](ontology.ttl) makes **30 alignment axioms** against **12
external vocabularies**. Each target was checked against the vocabulary's own
published file, not against a prefix table, a search result or memory. This
page is the evidence.

Run 2026-08-07. Script: `verify_iris.py` (fetches each vocabulary, parses it
with rdflib, and reports whether the term is present and what it is declared
as).

Why the fuss: the ontology team's own review of the rete datasets caught
`fabio:hasReference` and six invented CiTO terms that do not exist. An
unverifiable axiom is worse than no axiom — it looks like interoperability and
delivers nothing.

---

## Remote vocabularies — all present

| Vocabulary | Source fetched | Terms | Result |
|---|---|---|---|
| **FRAPO** `http://purl.org/cerif/frapo/` | `sparontologies.github.io/frapo/current/frapo.ttl` (1098 triples) | `Grant`, `FundingProgramme`, `hasGrantNumber`, `hasProjectIdentifier` | all **PRESENT** |
| **CiTO** `http://purl.org/spar/cito/` | `sparontologies.github.io/cito/current/cito.ttl` (946) | `cites` | **PRESENT** — `owl:ObjectProperty` |
| **FaBiO** `http://purl.org/spar/fabio/` | `sparontologies.github.io/fabio/current/fabio.ttl` (3322) | `Journal` | **PRESENT** — `owl:Class` |
| **DCMI Terms** `http://purl.org/dc/terms/` | `dublincore.org/.../dublin_core_terms.ttl` (700) | `abstract`, `accessRights`, `subject`, `identifier`, `title` | all **PRESENT** — `rdf:Property` |
| **SKOS** `http://www.w3.org/2004/02/skos/core#` | `w3.org/2009/08/skos-reference/skos.rdf` (253) | `Collection`, `altLabel`, `exactMatch`, `prefLabel` | all **PRESENT** |
| **W3C ORG** `http://www.w3.org/ns/org#` | `w3.org/ns/org.ttl` (748) | `Organization`, `OrganizationalUnit`, `Membership`, `unitOf` | all **PRESENT** |
| **FOAF** `http://xmlns.com/foaf/0.1/` | `xmlns.com/foaf/spec/index.rdf` (632) | `Project`, `Person`, `Organization` | all **PRESENT** — `owl:Class` |
| **VIVO** `http://vivoweb.org/ontology/core#` | `vivo-ontologies/vivo-ontology@master/vivo.owl` (6810) | `sponsorAwardId`, `Grant`, `FundingOrganization` | all **PRESENT** |
| **SPDX** `http://spdx.org/rdf/terms#` | `spdx.org/rdf/terms/spdx-ontology.owl.ttl` (1921) | `Package`, `Relationship`, `versionInfo` | all **PRESENT** |
| **CodeMeta** `https://codemeta.github.io/terms/` | `w3id.org/codemeta/3.0` @context (HTTP 200) | `readme`, `issueTracker`, `developmentStatus`, `continuousIntegration`, `buildInstructions` | all **PRESENT** in the context |

## Local vocabularies — all present

| Vocabulary | Source | Terms | Result |
|---|---|---|---|
| **deps.dev** `https://w3id.org/rete/deps-dev#` | `data/deps-dev/deps-dev.ttl` in the rete repo (118 triples) | `PackageVersion`, `Advisory`, `dependsOn`, `dependencyOf`, `hasProject`, `hasAdvisory`, `affects`, `purl`, `system`, `registry`, `isRelease`, `severity`, `advisorySource` | all 13 **PRESENT** |
| **rete scholar hub** `https://w3id.org/rete/scholar#` | [`../rete/scholar.ttl`](../rete/scholar.ttl) (139 triples) | `Grant`, `Project`, `Work`, `Person`, `Organization` | all **PRESENT** |

---

## Findings that changed the proposal

**1. `spdx:Package` exists — the alignment is now asserted.** It was commented
out as unverifiable for several revisions (LOV's term API is down, so no
vocabulary sweep was possible). Verified directly: `owl:Class` in the SPDX
ontology. Since our dependency data *is* an SPDX SBOM, one axiom buys the SBOM
ecosystem. **Caveat recorded in the file:** the verified ontology is **2.3**
(`versionIRI http://spdx.org/rdf/terms/2.3`); SPDX 3.0 moved to a different
namespace, so the axiom needs re-pointing when 3.0 is adopted.

**2. CodeMeta's own namespace is `https://codemeta.github.io/terms/`** — not
`w3id.org/codemeta/…`, which is the *context* URL. With that settled, three
alignments became assertable and CodeMeta showed up two terms we lack:
`pulse:issueTracker` (where the tracker *is*, as opposed to §3's
`hasIssues`, which only says one exists) and `pulse:developmentStatus`
(repostatus.org values — "is this maintained?" is the first question asked of
research software, and `pulse:archived` only catches the case where somebody
remembered to archive).

**3. `frapo:hasGrantNumber` declares no `rdfs:range`.** A secondary source
claimed `xsd:decimal`; the published Turtle has no range at all. Good news —
no datatype clash with grant numbers like `200021_192356`, which are not
decimals. The false claim is not propagated.

**4. `vivo:sponsorAwardId` is `owl:FunctionalProperty`.** Asserting
`pulse:awardNumber ⊑ vivo:sponsorAwardId` therefore implies at most one award
number per grant. Fine for a single funder's identifier, wrong for a
co-funded project — noted in the file so nobody trips over it later.

**5. schema.org is spelled two ways across these ontologies** — the one
finding that breaks real integration:

```
ontology-definitions-raw.ttl        @prefix schema: <http://schema.org/>
ontology-shapes-raw.ttl             @prefix schema: <http://schema.org/>
ontology-definitions-canonical.ttl  @prefix schema: <http://schema.org/>
dev/rete/epfl-infoscience.ttl       @prefix schema: <https://schema.org/>
```

In RDF these are different IRIs. `infs:Person ⊑ https://schema.org/Person` and
a shape targeting `http://schema.org/Person` **do not unify** — an Infoscience
person and an Open Pulse person land in separate components of a union graph,
silently, with no error. Raised as §22 and ask #21. This proposal uses
`http://schema.org/` throughout, matching Open Pulse.

---

## What is still unverified

Stated so the gaps are visible rather than implied.

- **`oaire:` cannot be aligned at all** — not a gap, a settled negative.
  `oaire.xsd` is pure XSD (zero `rdf:` in the body),
  `namespace.openaire.eu` is NXDOMAIN from Google Public DNS, and OpenAIRE's
  LOD service closed 2023-05-08. Recorded in §20 as `rdfs:seeAlso` plus prose,
  never as an axiom. Same for DataCite `FundingReference` (XML and JSON only).
- **LOV's term API returns 404** for every query, so no systematic sweep for
  *unknown* candidate vocabularies was possible. Everything above came from
  primary sources; there may be a better alignment target that nobody checked.
- **euroCRIS successor RDF** (`eurocris.org/ontologies/semcerif`,
  `w3id.org/cerif/model`, `w3id.org/cerif/vocab/`) — cited by third parties,
  not dereference-tested. FRAPO is self-described as CERIF-compliant and is
  actively published, so it was preferred.
- **No SNSF funding-instrument vocabulary in RDF** was found. SNSF grant
  numbers stay plain literals under `frapo:hasGrantNumber`, with the funder
  identified by its ROR or Crossref Funder ID.
- **EU programme values**: `…/authority/eu-programme/H2020` verified live as a
  `skos:Concept`; `HORIZONEU` confirmed present in the scheme but its
  individual `prefLabel` was not retrieved.

## Reproducing this

```bash
python verify_iris.py     # in dev/v4.0.0/ — fetches each vocabulary and prints
                          # PRESENT/ABSENT + declared type per term
```

The script is deliberately dumb: fetch, parse, look up the IRI, print what the
publisher says it is. If a vocabulary moves or a term is withdrawn, it fails
loudly rather than leaving a stale axiom in the proposal.
