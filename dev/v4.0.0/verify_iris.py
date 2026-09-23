"""Verify every external IRI our ontology proposal asserts against its
published source. Prints PRESENT/ABSENT plus the declared rdf:type."""
from __future__ import annotations
import sys, warnings
warnings.filterwarnings("ignore")
import requests
from rdflib import Graph, RDF, URIRef

TARGETS = {
    "http://purl.org/cerif/frapo/": (
        ["https://sparontologies.github.io/frapo/current/frapo.ttl"],
        ["FundingProgramme", "Grant", "hasGrantNumber", "hasProjectIdentifier"],
    ),
    "http://purl.org/spar/cito/": (
        ["https://sparontologies.github.io/cito/current/cito.ttl"],
        ["cites"],
    ),
    "http://purl.org/spar/fabio/": (
        ["https://sparontologies.github.io/fabio/current/fabio.ttl"],
        ["Journal"],
    ),
    "http://purl.org/dc/terms/": (
        ["https://www.dublincore.org/specifications/dublin-core/dcmi-terms/dublin_core_terms.ttl",
         "https://raw.githubusercontent.com/dcmi/dcmi-terms/main/dublin_core_terms.ttl"],
        ["abstract", "accessRights", "subject", "identifier", "title"],
    ),
    "http://www.w3.org/2004/02/skos/core#": (
        ["https://www.w3.org/2009/08/skos-reference/skos.rdf"],
        ["Collection", "altLabel", "exactMatch", "prefLabel"],
    ),
    "http://www.w3.org/ns/org#": (
        ["https://www.w3.org/ns/org.ttl", "http://www.w3.org/ns/org#"],
        ["Organization", "OrganizationalUnit", "Membership", "unitOf"],
    ),
    "http://xmlns.com/foaf/0.1/": (
        ["http://xmlns.com/foaf/spec/index.rdf"],
        ["Project", "Person", "Organization"],
    ),
    "http://vivoweb.org/ontology/core#": (
        ["https://raw.githubusercontent.com/vivo-ontologies/vivo-ontology/master/vivo.owl"],
        ["sponsorAwardId", "Grant", "FundingOrganization"],
    ),
    "http://spdx.org/rdf/terms#": (   # the §13 TO-VERIFY
        ["https://spdx.org/rdf/terms/spdx-ontology.owl.ttl",
         "https://raw.githubusercontent.com/spdx/spdx-spec/development/v2.3.1/ontology/spdx-ontology.owl.ttl"],
        ["Package", "Relationship", "versionInfo"],
    ),
}

def load(urls):
    for u in urls:
        try:
            r = requests.get(u, timeout=60, headers={"Accept": "text/turtle, application/rdf+xml;q=0.9, */*;q=0.1"})
            if r.status_code != 200:
                print(f"    [{r.status_code}] {u}"); continue
            g = Graph()
            for fmt in ("turtle", "xml"):
                try:
                    g.parse(data=r.text, format=fmt); return g, u
                except Exception:
                    continue
            print(f"    [unparseable] {u}")
        except Exception as e:
            print(f"    [error] {u}: {type(e).__name__}")
    return None, None

for ns, (urls, terms) in TARGETS.items():
    print(f"\n### {ns}")
    g, src = load(urls)
    if g is None:
        print("    COULD NOT VERIFY — no source loaded")
        continue
    print(f"    source: {src}  ({len(g)} triples)")
    for t in terms:
        iri = URIRef(ns + t)
        types = [str(o).split("#")[-1].split("/")[-1] for o in g.objects(iri, RDF.type)]
        present = bool(types) or (iri, None, None) in g
        print(f"      {'PRESENT' if present else 'ABSENT ':8} {t:22} {','.join(types) if types else ''}")
