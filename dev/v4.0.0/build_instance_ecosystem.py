"""Close the last gaps: Community, Advisory, ContainerImage, conceptDoi.

Sources, all real and all local except the advisories:
  data/index/zenodo/duckdb      6,758 records, 720 communities, 16,087 creators
  data/index/dockerhub/duckdb   indexed Docker Hub images
  deps.dev rete shard           real GHSA/OSV advisories, queried live

Exercises pulse:Community + memberOfCommunity, pulse:conceptDoi,
pulse:accessRights, pulse:Advisory + hasAdvisory/advisorySource/advisorySeverity,
and pulse:ContainerImage — the node types the conversion test could not reach
before.

Run after the other three builders.
"""
from __future__ import annotations

import glob
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "examples" / "gimie-raw-instance.ttl"
RUN = "urn:pulse:run:gimie-baseline"
WHEN = "2026-08-07T00:00:00Z"

L: list[str] = []
add = L.append


def esc(s) -> str:
    return " ".join(str(s).replace("\\", "\\\\").replace('"', '\\"').split())


def db(name: str):
    fs = glob.glob(str(ROOT / "data" / "index" / name / "duckdb" / "*.duckdb"))
    return duckdb.connect(fs[0], read_only=True) if fs else None


add("\n# =====================================================================")
add("# ECOSYSTEM TAIL — Community, Advisory, ContainerImage, conceptDoi.")
add("# Appended by build_instance_ecosystem.py — do not hand-edit.")
add("# =====================================================================\n")

# ------------------------------------------------------------- Zenodo depth
out_zen = "urn:pulse:output:gimie-baseline:zenodo"
z = db("zenodo")
if z is not None:
    rows = z.execute("""
        SELECT r.zenodo_id, r.concept_recid, r.doi, r.title, r.access_right,
               r.license_id, c.community_id, c.title
        FROM records r
        JOIN record_communities rc ON r.zenodo_id = rc.record_id
        JOIN communities c ON rc.community_id = c.community_id
        WHERE r.resource_type = 'software' AND r.doi IS NOT NULL
        LIMIT 2
    """).fetchall()

    seen_comm: set[str] = set()
    add(f"# --- Zenodo depth: {len(rows)} real EPFL software records with their community ---")
    for zid, concept, doi, title, access, lic, comm_id, comm_title in rows:
        access_iri = {
            "open": "info:eu-repo/semantics/openAccess",
            "embargoed": "info:eu-repo/semantics/embargoedAccess",
            "restricted": "info:eu-repo/semantics/restrictedAccess",
            "closed": "info:eu-repo/semantics/closedAccess",
        }.get(str(access), "info:eu-repo/semantics/openAccess")
        concept_doi = f"https://doi.org/10.5281/zenodo.{concept}" if concept else None
        add(f"""<{doi}> a schema:ScholarlyArticle ;
    schema:name "{esc(title)[:140]}" ;
    pulse:doi "{esc(str(doi).replace('https://doi.org/', ''))}" ;""" +
            (f'\n    pulse:conceptDoi "{esc(str(concept_doi).replace("https://doi.org/", ""))}" ;' if concept_doi else "") +
            f"""
    pulse:accessRights <{access_iri}> ;
    schema:license <https://spdx.org/licenses/{esc(lic).upper()}.html> ;
    pulse:memberOfCommunity <{comm_id}> ;
    pulse:partOfRun <{out_zen}> .""")
        if comm_id not in seen_comm:
            seen_comm.add(comm_id)
            add(f"""<{comm_id}> a pulse:Community ;
    schema:name "{esc(comm_title)}" ;
    schema:url <{comm_id}> ;
    pulse:platformInternalId "{esc(str(comm_id).rsplit('/', 1)[-1])}" ;
    pulse:partOfRun <{out_zen}> .""")

    # creators carry real ORCIDs — the Person layer, from a third platform
    creators = z.execute("""
        SELECT display_name, orcid, affiliation FROM creators
        WHERE orcid IS NOT NULL AND affiliation IS NOT NULL LIMIT 2
    """).fetchall()
    add("\n# --- Zenodo creators: real ORCIDs, so they merge with the ORCID layer ---")
    for name, orcid, aff in creators:
        add(f"""<{orcid}> a schema:Person ;
    schema:name "{esc(name)}" ;
    pulse:orcidIdentifier "{esc(str(orcid).rsplit('/', 1)[-1])}" ;
    pulse:partOfRun <{out_zen}> .
# affiliation string as given by Zenodo: "{esc(aff)}\"""")
    z.close()
    add("")

# ------------------------------------------------------------- Docker Hub
out_dh = "urn:pulse:output:gimie-baseline:dockerhub"
d = db("dockerhub")
if d is not None:
    imgs = d.execute("SELECT namespace, name, description, star_count FROM images").fetchall()
    d.close()
    if imgs:
        add(f"""<{out_dh}> a pulse:ExtractionOutput ;
    prov:wasGeneratedBy <{RUN}> ;
    pulse:platform pulse:DockerHub ;
    prov:generatedAtTime "{WHEN}"^^xsd:dateTime .

# --- Docker Hub: {len(imgs)} images from our own index. NOT published by the
# repository under test — these are what the dockerhub index actually holds,
# so they are attached as referenced images rather than invented as ours. ---""")
        for ns, nm, desc, stars in imgs:
            ref = f"{ns}/{nm}"
            add(f"""<urn:pulse:image:{esc(ref)}> a pulse:ContainerImage ;
    pulse:imageReference "{esc(ref)}" ;
    pulse:platform pulse:DockerHub ;
    schema:description "{esc(desc)[:110]}" ;
    schema:url <https://hub.docker.com/r/{esc(ref)}> ;
    pulse:partOfRun <{out_dh}> .""")
        add("")

# --------------------------------------------------------------- advisories
# Queried live from the deps.dev PyPI shard (2.55bn-triple graph):
#   ?pv deps:purl "pkg:pypi/urllib3@0.3.0" ; deps:hasAdvisory ?adv
# Three real GHSA advisories, source OSV.
out_deps = "urn:pulse:output:gimie-baseline:depsdev"
ADVISORIES = ["GHSA-34jh-p97f-mpxf", "GHSA-g4mx-q9vg-27p4", "GHSA-gwvm-45gx-3cf8"]
URLLIB3 = "https://deps.dev/pypi/urllib3/0.3.0"
add(f"""<{out_deps}> a pulse:ExtractionOutput ;
    prov:wasGeneratedBy <{RUN}> ;
    pulse:platform pulse:DepsDev ;
    prov:generatedAtTime "{WHEN}"^^xsd:dateTime .

# --- security advisories (§19): real GHSA records from deps.dev, on a real
# package version. This is the layer neither profile can express today. ---
<{URLLIB3}> a pulse:Package ;
    schema:name "urllib3" ;
    pulse:packageIdentifier "pkg:pypi/urllib3@0.3.0" ;
    pulse:packageEcosystem pulse:PyPI ;
    pulse:partOfRun <{out_deps}> .""")
for ghsa in ADVISORIES:
    iri = f"https://github.com/advisories/{ghsa}"
    add(f"""<{URLLIB3}> pulse:hasAdvisory <{iri}> .
<{iri}> a pulse:Advisory ;
    schema:identifier "{ghsa}" ;
    pulse:advisorySource "OSV" ;
    pulse:advisorySeverity "UNKNOWN" ;
    pulse:advisoryAffects <{URLLIB3}> ;
    pulse:partOfRun <{out_deps}> .""")

add(f"""
<urn:pulse:obs:adv:0> a pulse:Observation ;
    pulse:observedSubject <{URLLIB3}> ;
    pulse:observedProperty pulse:hasAdvisory ;
    pulse:observedValue "{ADVISORIES[0]}" ;
    pulse:retrievedFrom <https://data.graphplaza.com/deps-dev/deps-dev-pypi.rete> ;
    pulse:retrievedAt "{WHEN}"^^xsd:dateTime ;
    pulse:sourcePlatform pulse:DepsDev ;
    pulse:observationKind "single-source" .""")

with OUT.open("a", encoding="utf-8") as fh:
    fh.write("\n".join(L) + "\n")
print(f"appended ecosystem tail ({len(L)} blocks)")
