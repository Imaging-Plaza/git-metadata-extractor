"""Add the funding platforms to the instance graph: SNSF (FNS) and CORDIS.

SNSF   real rows from data/index/snsf/raw/grants.csv — the P3 bulk CSV that
       our own ingester expects (open_pulse_sources/index/snsf/ingest/
       local_ingest.py reads exactly this file). The DuckDB store built from
       it is empty, so the CSV is the live source here.
CORDIS real EU project records via OpenAIRE, which aggregates CORDIS.
       Captured to examples/sources/cordis_openaire.json.

Exercises: schema:Project, pulse:awardNumber, pulse:fundingProgramme,
pulse:grantAgreementIdentifier, pulse:Funding, fundsProject/fundedBy, and the
new pulse:CORDIS / pulse:SNSF_P3 platform members.

Run after the other two builders.
"""
from __future__ import annotations

import csv
import itertools
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "examples" / "gimie-raw-instance.ttl"
SNSF_CSV = ROOT / "data" / "index" / "snsf" / "raw" / "grants.csv"
CORDIS = HERE / "examples" / "sources" / "cordis_openaire.json"

RUN = "urn:pulse:run:gimie-baseline"
EPFL_ROR = "https://ror.org/02s376052"

L: list[str] = []
add = L.append


def esc(s) -> str:
    return " ".join(str(s).replace("\\", "\\\\").replace('"', '\\"').split())


add("\n# =====================================================================")
add("# FUNDING PLATFORMS — SNSF (FNS) and CORDIS, from real records.")
add("# Appended by build_instance_funding.py — do not hand-edit.")
add("# =====================================================================\n")

# ------------------------------------------------------------------- SNSF
out_snsf = "urn:pulse:output:gimie-baseline:snsf_p3"
add(f"""<{out_snsf}> a pulse:ExtractionOutput ;
    prov:wasGeneratedBy <{RUN}> ;
    pulse:platform pulse:SNSF_P3 ;
    prov:generatedAtTime "2026-08-07T00:00:00Z"^^xsd:dateTime .
""")

rows: list[dict] = []
if SNSF_CSV.exists():
    with SNSF_CSV.open(encoding="utf-8-sig", newline="") as fh:
        head = fh.read(4096)
        fh.seek(0)
        delim = ";" if head.count(";") > head.count(",") else ","
        for row in itertools.islice(csv.DictReader(fh, delimiter=delim), 4000):
            inst = (row.get("ResearchInstitution") or "") + (row.get("Institute") or "")
            if "EPFL" in inst:
                rows.append(row)
            if len(rows) >= 2:
                break

add(f"# --- SNSF / FNS: {len(rows)} real EPFL grants from the P3 bulk CSV ---")
for row in rows:
    gnum = (row.get("GrantNumber") or "").strip()
    proj = f"https://data.snf.ch/grants/grant/{gnum}"
    title = row.get("TitleEnglish") or row.get("Title") or ""
    instrument = row.get("FundingInstrumentPublished") or ""
    add(f"""<{proj}> a schema:Project ;
    schema:name "{esc(title)[:150]}" ;
    pulse:awardNumber "{esc(gnum)}" ;
    pulse:fundingProgramme "{esc(instrument)}" ;
    schema:sourceOrganization <{EPFL_ROR}> ;
    schema:funder <https://ror.org/00yjd3n13> ;
    pulse:fundedBy <urn:pulse:funding:snsf:{esc(gnum)}> ;
    pulse:partOfRun <{out_snsf}> .

<urn:pulse:funding:snsf:{esc(gnum)}> a pulse:Funding ;
    schema:name "{esc(instrument)}" ;
    pulse:awardNumber "{esc(gnum)}" ;
    pulse:fundsProject <{proj}> ;
    schema:funder <https://ror.org/00yjd3n13> ;
    pulse:partOfRun <{out_snsf}> .

<urn:pulse:obs:snsf:{esc(gnum)}> a pulse:Observation ;
    pulse:observedSubject <{proj}> ;
    pulse:observedProperty pulse:awardNumber ;
    pulse:observedValue "{esc(gnum)}" ;
    pulse:retrievedFrom <https://data.snf.ch/grants> ;
    pulse:retrievedAt "2026-08-07T00:00:00Z"^^xsd:dateTime ;
    pulse:sourcePlatform pulse:SNSF_P3 ;
    pulse:observationKind "bulk-export" .
""")

# the funder itself, named by its ROR
add("""# the funder, identified the way we identify every organization
<https://ror.org/00yjd3n13> a org:Organization ;
    schema:name "Swiss National Science Foundation" ;
    pulse:acronym "SNSF" , "FNS" .
""")

# ----------------------------------------------------------------- CORDIS
out_cordis = "urn:pulse:output:gimie-baseline:cordis"
add(f"""<{out_cordis}> a pulse:ExtractionOutput ;
    prov:wasGeneratedBy <{RUN}> ;
    pulse:platform pulse:CORDIS ;
    prov:generatedAtTime "2026-08-07T00:00:00Z"^^xsd:dateTime .
""")

if CORDIS.exists():
    d = json.loads(CORDIS.read_text(encoding="utf-8"))
    res = d.get("response", {}).get("results", {}).get("result", [])
    res = res if isinstance(res, list) else [res]

    def val(x):
        return x.get("$") if isinstance(x, dict) else x

    add(f"# --- CORDIS via OpenAIRE: {len(res)} real EU projects ---")
    for r in res[:2]:
        md = r["metadata"]["oaf:entity"]["oaf:project"]
        code = val(md.get("code")) or ""
        acronym = val(md.get("acronym")) or ""
        title = val(md.get("title")) or ""
        start, end = val(md.get("startdate")), val(md.get("enddate"))
        ft = md.get("fundingtree")
        ft = (ft[0] if isinstance(ft, list) else ft) or {}
        funder = val((ft.get("funder") or {}).get("shortname")) or "EC"
        prog = val((ft.get("funding_level_0") or {}).get("name")) or "HORIZON"
        proj = f"https://cordis.europa.eu/project/id/{code}"
        ga = f"info:eu-repo/grantAgreement/{funder}/{prog}/{code}"
        add(f"""<{proj}> a schema:Project ;
    schema:name "{esc(title)[:150]}" ;
    pulse:acronym "{esc(acronym)}" ;
    pulse:awardNumber "{esc(code)}" ;
    pulse:grantAgreementIdentifier "{esc(ga)}" ;
    pulse:fundingProgramme "{esc(prog)}" ;
    schema:funder <urn:pulse:funder:{esc(funder)}> ;""" +
            (f'\n    time:hasBeginning "{start}"^^xsd:date ;' if start else "") +
            (f'\n    time:hasEnd "{end}"^^xsd:date ;' if end else "") + f"""
    pulse:partOfRun <{out_cordis}> .

<urn:pulse:funder:{esc(funder)}> a org:Organization ;
    schema:name "European Commission" ;
    pulse:acronym "{esc(funder)}" .

<urn:pulse:obs:cordis:{esc(code)}> a pulse:Observation ;
    pulse:observedSubject <{proj}> ;
    pulse:observedProperty pulse:grantAgreementIdentifier ;
    pulse:observedValue "{esc(ga)}" ;
    pulse:retrievedFrom <https://api.openaire.eu/search/projects?funder=EC> ;
    pulse:retrievedAt "2026-08-07T00:00:00Z"^^xsd:dateTime ;
    pulse:sourcePlatform pulse:CORDIS ;
    pulse:observationKind "single-source" .
""")

with OUT.open("a", encoding="utf-8") as fh:
    fh.write("\n".join(L) + "\n")
print(f"appended funding section ({len(L)} blocks): {len(rows)} SNSF grants, CORDIS projects")
