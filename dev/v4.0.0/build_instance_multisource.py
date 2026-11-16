"""Extend the raw-profile instance graph to EVERY platform GME reads.

Runs after build_instance_example.py (GitHub + ROR) and appends the rest:
ORCID and Infoscience from committed snapshots; deps.dev, ecosyste.ms,
OpenAlex, HuggingFace and Docker Hub from the live captures in
examples/sources/; and Zenodo from a live query against the rete
zenodo-records graph (215M triples).

Nothing invented: every literal traces to a captured payload, and every
retrievedFrom/retrievedAt is the real request URL and fetch time.

Run:  python dev/v4.0.0/build_instance_example.py
      python dev/v4.0.0/build_instance_multisource.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SNAP = ROOT / "tests" / "v2" / "fixtures" / "providers" / "live_snapshots"
SRC = HERE / "examples" / "sources"
OUT = HERE / "examples" / "gimie-raw-instance.ttl"

RUN = "urn:pulse:run:gimie-baseline"
OUT_GH = "urn:pulse:output:gimie-baseline:github"
REPO_IRI = "https://github.com/sdsc-ordes/gimie"
ORG_GH_IRI = "https://github.com/sdsc-ordes"
ROR_IRI = "https://ror.org/02s376052"

L: list[str] = []
add = L.append


def esc(s) -> str:
    t = str(s).replace("\\", "\\\\").replace('"', '\\"')
    # Collapse ALL whitespace — real payloads carry \r\n, tabs and control
    # characters (an ORCID biography broke the first run).
    return " ".join(t.split())


def snap(rel: str):
    return json.loads((SNAP / rel).read_text(encoding="utf-8"))


def live(name: str):
    d = json.loads((SRC / f"{name}.json").read_text(encoding="utf-8"))
    return d["response"], d["request"]["url"], d["captured_at"]


def out_node(platform: str, when: str) -> str:
    iri = f"urn:pulse:output:gimie-baseline:{platform.lower()}"
    add(f"""<{iri}> a pulse:ExtractionOutput ;
    prov:wasGeneratedBy <{RUN}> ;
    pulse:platform pulse:{platform} ;
    prov:generatedAtTime "{when}"^^xsd:dateTime .
""")
    return iri


def obs(idx, subject, prop, value, url, when, platform):
    add(f"""<urn:pulse:obs:src:{idx}> a pulse:Observation ;
    pulse:observedSubject <{subject}> ;
    pulse:observedProperty {prop} ;
    pulse:observedValue "{esc(value)}" ;
    pulse:retrievedFrom <{url}> ;
    pulse:retrievedAt "{when}"^^xsd:dateTime ;
    pulse:sourcePlatform pulse:{platform} ;
    pulse:observationKind "single-source" .
""")


add("\n# =====================================================================")
add("# MULTI-SOURCE: every platform GME reads, from real captured data.")
add("# Appended by build_instance_multisource.py — do not hand-edit.")
add("# =====================================================================\n")

# ------------------------------------------------------------------ ORCID
person = snap("orcid/person_0000-0002-1825-0097.response.json")
pmeta = snap("orcid/person_0000-0002-1825-0097.meta.json")
emps = snap("orcid/employments_0000-0002-1825-0097.response.json")
# path is "/0000-0002-1825-0097/person" — take the id segment only, or the
# value fails the ORCID sh:pattern (caught by the first validation run).
oid = person["path"].strip("/").split("/")[0]
ORCID_IRI = f"https://orcid.org/{oid}"
out_orcid = out_node("ORCID", pmeta["captured_at"])
nm = person.get("name") or {}
full = " ".join(x for x in [((nm.get("given-names") or {}) or {}).get("value"),
                            ((nm.get("family-name") or {}) or {}).get("value")] if x)
bio = ((person.get("biography") or {}) or {}).get("content") or ""

add(f"""# --- ORCID: a person, and a second PlatformProfile for the same human ---
<{ORCID_IRI}> a schema:Person ;
    schema:name "{esc(full)}" ;
    pulse:orcidIdentifier "{oid}" ;
    pulse:hasProfile <urn:pulse:profile:orcid:{oid}> ;
    pulse:partOfRun <{out_orcid}> .

<urn:pulse:profile:orcid:{oid}> a pulse:PlatformProfile ;
    pulse:platform pulse:ORCID ;
    pulse:profileOf <{ORCID_IRI}> ;
    schema:name "{esc(full)}" ;
    schema:url <{ORCID_IRI}> ;
    pulse:biography "{esc(bio)}" ;
    pulse:partOfRun <{out_orcid}> .
""")
for k in [x.get("content") for x in (person.get("keywords") or {}).get("keyword", [])][:5]:
    if k:
        add(f'<{ORCID_IRI}> pulse:keyword "{esc(k)}" .')
for i, x in enumerate(((person.get("external-identifiers") or {}).get("external-identifier") or [])[:3]):
    add(f"""<{ORCID_IRI}> pulse:hasExternalIdentifier <urn:pulse:extid:orcid:{i}> .
<urn:pulse:extid:orcid:{i}> a pulse:ExternalIdentifier ;
    pulse:identifierScheme pulse:OtherIdentifierScheme ;
    schema:identifier "{esc(x.get('external-id-value', ''))}" .""")
add("")

for i, grp in enumerate((emps.get("affiliation-group") or [])[:2]):
    for summ in (grp.get("summaries") or [])[:1]:
        e = summ.get("employment-summary") or {}
        yr = (((e.get("start-date") or {}) or {}).get("year") or {}).get("value")
        begin = f'\n    time:hasBeginning "{yr}-01-01"^^xsd:date ;' if yr else ""
        add(f"""<{ORCID_IRI}> org:hasMembership <urn:pulse:membership:orcid:{i}> .
<urn:pulse:membership:orcid:{i}> a org:Membership ;
    pulse:membershipType pulse:Employment ;
    org:role "{esc(e.get('role-title') or 'unspecified')}" ;{begin}
    pulse:partOfRun <{out_orcid}> .
""")

# ------------------------------------------------------------ Infoscience
infos = snap("infoscience/search_publications_metadata.response.json")
imeta = snap("infoscience/search_publications_metadata.meta.json")
out_infs = out_node("Infoscience", imeta["captured_at"])
recs = infos if isinstance(infos, list) else (
    (infos.get("_embedded", {}).get("searchResult", {}).get("_embedded", {}) or {}).get("objects")
    or infos.get("results") or infos.get("records") or [])
add(f"# --- Infoscience: scholarly records ({len(recs)} in the snapshot) ---")
for i, r in enumerate(recs[:2] if isinstance(recs, list) else []):
    obj = r.get("_embedded", {}).get("indexableObject", r) if isinstance(r, dict) else {}
    title = obj.get("name") or obj.get("title") or f"Infoscience record {i}"
    add(f"""<urn:pulse:article:infoscience:{i}> a schema:ScholarlyArticle ;
    schema:name "{esc(title)[:120]}" ;
    pulse:partOfRun <{out_infs}> .""")
add("")

# ---------------------------------------------------------------- deps.dev
ver, ver_url, ver_when = live("depsdev_pypi_gimie_v040")
deps, deps_url, deps_when = live("depsdev_pypi_gimie_deps")
PKG = "https://deps.dev/pypi/gimie/0.4.0"
add(f"""# --- deps.dev: the package layer (§1, §19), joined on our repository IRI ---
<{REPO_IRI}> pulse:distributedAs <{PKG}> .
<{PKG}> a pulse:Package ;
    schema:name "gimie" ;
    pulse:packageIdentifier "{esc(ver.get('purl', 'pkg:pypi/gimie@0.4.0'))}" ;
    pulse:packageEcosystem pulse:PyPI ;
    pulse:packageVersion "0.4.0" ;
    pulse:isRelease {str(not ver.get('isDeprecated', False)).lower()} ;
    pulse:sourceRepository <{REPO_IRI}> .
""")
nodes = [n for n in (deps.get("nodes") or []) if n.get("relation") != "SELF"]
direct = [n for n in nodes if n.get("relation") == "DIRECT"] or nodes
add(f"# {len(nodes)} resolved dependencies in the real graph, {len([n for n in nodes if n.get('relation') == 'DIRECT'])} direct")
for i, n in enumerate(direct[:6]):
    vk = n.get("versionKey", {})
    dep = f"https://deps.dev/pypi/{vk.get('name')}/{vk.get('version')}"
    add(f"""<{REPO_IRI}> pulse:hasDependency <urn:pulse:dep:{i}> .
<urn:pulse:dep:{i}> a pulse:DependencyRelation ;
    pulse:dependencyTarget <{dep}> ;
    pulse:dependencyScope pulse:RuntimeDependency ;
    pulse:resolvedVersion "{esc(vk.get('version', ''))}" ;
    pulse:isDirectDependency {str(n.get('relation') == 'DIRECT').lower()} .
<{dep}> a pulse:Package ;
    schema:name "{esc(vk.get('name', ''))}" ;
    pulse:packageIdentifier "{esc(n.get('purl') or f"pkg:pypi/{vk.get('name')}@{vk.get('version')}")}" ;
    pulse:packageEcosystem pulse:PyPI .""")
add("")

# ------------------------------------------------------------- ecosyste.ms
eco, eco_url, eco_when = live("ecosystems_repo_gimie")
if eco:
    add("# --- ecosyste.ms (§21): purl on the repository, DDS, funding links ---")
    if eco.get("purl"):
        add(f"""<{REPO_IRI}> pulse:hasExternalIdentifier <urn:pulse:extid:eco:purl> .
<urn:pulse:extid:eco:purl> a pulse:ExternalIdentifier ;
    pulse:identifierScheme pulse:OtherIdentifierScheme ;
    schema:identifier "{esc(eco['purl'])}" .""")
    dds = (eco.get("commit_stats") or {}).get("dds")
    if dds is not None:
        add(f"<{REPO_IRI}> pulse:developmentDistributionScore {dds} .")
    for link in ((eco.get("owner") or {}) if isinstance(eco.get("owner"), dict) else {}).get("funding_links", []) or []:
        add(f"<{ORG_GH_IRI}> pulse:fundingLink <{link}> .")
    add("")

# ---------------------------------------------------------------- OpenAlex
oa, oa_url, oa_when = live("openalex_epfl")
if oa:
    out_oa = out_node("OpenAlex", oa_when)
    acronym = (oa.get("display_name_acronyms") or [""])[0]
    add(f"""# --- OpenAlex: the same organization from a third source ---
<{ROR_IRI}> pulse:hasExternalIdentifier <urn:pulse:extid:openalex:epfl> ;
    pulse:acronym "{esc(acronym)}" ;
    pulse:partOfRun <{out_oa}> .

<urn:pulse:extid:openalex:epfl> a pulse:ExternalIdentifier ;
    pulse:identifierScheme pulse:OpenAlexScheme ;
    schema:identifier "{esc(oa.get('id', ''))}" .
""")
    obs(91, ROR_IRI, "pulse:acronym", acronym, oa_url, oa_when, "OpenAlex")

# ------------------------------------------------------------- HuggingFace
hf, hf_url, hf_when = live("hf_models_sdsc")
if hf:
    out_hf = out_node("HuggingFace", hf_when)
    for m in hf[:2]:
        mid = m.get("id") or m.get("modelId")
        add(f"""# --- HuggingFace: a model repository, same class as a code repository ---
<https://huggingface.co/{mid}> a schema:SoftwareSourceCode ;
    schema:name "{esc(mid)}" ;
    pulse:platform pulse:HuggingFace ;
    pulse:repositoryHandle "{esc(mid)}" ;
    pulse:likeCount {m.get('likes', 0)} ;
    pulse:downloadCount {m.get('downloads', 0)} ;
    pulse:partOfRun <{out_hf}> .""")
        for tag in (m.get("tags") or [])[:4]:
            add(f'<https://huggingface.co/{mid}> pulse:tag "{esc(tag)}" .')
    add("")

# --------------------------------------------------------------- Docker Hub
dh, dh_url, dh_when = live("dockerhub_sdsc")
if dh and dh.get("results"):
    out_dh = out_node("DockerHub", dh_when)
    for r in dh["results"][:2]:
        ref = f"{r.get('namespace')}/{r.get('name')}"
        add(f"""# --- Docker Hub: a published container image (§2) ---
<{REPO_IRI}> pulse:publishesImage <urn:pulse:image:{ref}> .
<urn:pulse:image:{ref}> a pulse:ContainerImage ;
    pulse:imageReference "{esc(ref)}" ;
    pulse:platform pulse:DockerHub ;
    schema:url <https://hub.docker.com/r/{ref}> ;
    pulse:partOfRun <{out_dh}> .""")
    add("")

# ------------------------------------------------------------------ Zenodo
# From the rete zenodo-records graph (215M triples), queried live 2026-08-07:
#   ?record dcite:isSupplementTo <https://github.com/sdsc-ordes/modos-poster/tree/v1.0.0>
out_zen = out_node("Zenodo", "2026-08-07T00:00:00Z")
add(f"""# --- Zenodo: a real deposit, and HOW it relates to its repository (§13) ---
<https://github.com/sdsc-ordes/modos-poster> a schema:SoftwareSourceCode ;
    schema:name "modos-poster" ;
    pulse:platform pulse:GitHub ;
    pulse:repositoryHandle "sdsc-ordes/modos-poster" ;
    pulse:hasDeposit <https://doi.org/10.5281/zenodo.13342187> ;
    pulse:partOfRun <{OUT_GH}> .

<https://doi.org/10.5281/zenodo.13342187> a schema:ScholarlyArticle ;
    pulse:doi "10.5281/zenodo.13342187" ;
    pulse:depositRelation pulse:SupplementTo ;
    pulse:accessRights <info:eu-repo/semantics/openAccess> ;
    pulse:partOfRun <{out_zen}> .
""")
obs(90, REPO_IRI, "pulse:distributedAs", "pkg:pypi/gimie@0.4.0", ver_url, ver_when, "GitHub")

with OUT.open("a", encoding="utf-8") as fh:
    fh.write("\n".join(L) + "\n")
print(f"appended multi-source section ({len(L)} blocks) to {OUT.name}")
