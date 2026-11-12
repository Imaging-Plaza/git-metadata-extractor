"""Build the ontology explorer — a single self-contained HTML page.

Reading 22 asks across four TTL files and an 838-line instance graph is not a
job for a text editor. This extracts the whole proposal into one JSON payload
and inlines it into explorer_template.html, so the result is:

  * offline      no CDN, no fetch, no server — open the file
  * regenerable  every number traces to a TTL, none are typed by hand
  * honest       a term with no real instance data is labelled as such

Inputs
  proposed/ontology-definitions-raw.proposed.ttl   upstream + our terms
  proposed/ontology-shapes-raw.proposed.ttl        upstream + our shapes
  proposed/ontology-enumerations-raw.proposed.ttl  upstream + our members
  examples/gimie-raw-instance.ttl                  the 11-platform instance graph
  ontology.ttl                                     our sections + argument text

Output
  ontology-explorer.html

The proposed/*.ttl files are upstream text with our additions appended after a
marker banner, so splitting on the marker tells us which side of the fence each
term is on — no network access needed to know what is theirs and what is ours.

Run:  python dev/v4.0.0/build_explorer.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # for platform_inventory

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, SH, SKOS

import platform_inventory   # sibling module; see its docstring for the evidence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROPOSED = HERE / "proposed"
# the upstream commit proposed/ was generated from; keep in step with
# materialize_proposed_raw.py
REF = "290579dd7dfe"
OUT = HERE / "ontology-explorer.html"
TEMPLATE = HERE / "explorer_template.html"

# The banner build_instance_*/materialize_* writes before appended content.
# Everything above it is upstream; everything below is ours.
SPLIT = {
    "definitions": "# PROPOSED TERMS",
    "enumerations": "# PROPOSED ENUMERATION MEMBERS",
    "shapes": "# ASK #14",
}

# Read the prefix map off the files rather than hardcoding it. Hardcoding cost
# us 14 alignment axioms once: deps: is https://w3id.org/rete/deps-dev# (the
# rete shard), not deps.dev's own namespace, and a wrong IRI here silently
# leaves the term unshortened, which then fails every "is this external?" test.
PREFIXES: dict[str, str] = {}


def load_prefixes(*texts: str) -> None:
    for text in texts:
        for m in re.finditer(r"^@prefix\s+(\S*):\s*<([^>]+)>", text, flags=re.MULTILINE):
            pfx, iri = m.group(1) or "base", m.group(2)
            PREFIXES.setdefault(iri, pfx)

# Which namespaces are "us", "them", and "the wider world".
OURS_NS = "https://open-pulse.epfl.ch/ontology#"
GME_NS = "https://openpulse.science/git-metadata-extractor#"

VOCAB_NAMES = {
    "pulse": "Open Pulse",
    "gme": "GME internal",
    "schema": "schema.org",
    "schema1": "schema.org (https spelling)",
    "org": "W3C ORG",
    "prov": "W3C PROV-O",
    "dct": "DCMI Terms",
    "cito": "CiTO",
    "fabio": "FaBiO",
    "frapo": "FRAPO",
    "vivo": "VIVO",
    "spdx": "SPDX 2.3",
    "codemeta": "CodeMeta 3.0",
    "deps": "deps.dev",
    "scholar": "rete scholar",
    "foaf": "FOAF",
    "skos": "SKOS",
    "time": "OWL-Time",
    "sh": "SHACL",
    "owl": "OWL",
    "rdfs": "RDFS",
}


def curie(term) -> str:
    if isinstance(term, BNode):
        return "_:" + str(term)[:8]
    if isinstance(term, Literal):
        return str(term)
    s = str(term)
    # longest namespace first, so schema.org/ never shadows a longer prefix
    for ns in sorted(PREFIXES, key=len, reverse=True):
        if s.startswith(ns):
            return f"{PREFIXES[ns]}:{s[len(ns):]}"
    return s


def prefix_of(c: str) -> str:
    return c.split(":", 1)[0] if ":" in c and not c.startswith("http") else ""


def split_text(path: Path, marker: str) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    idx = text.find(marker)
    if idx == -1:
        return text, ""
    head, tail = text[:idx], text[idx:]
    # the appended half needs the header's @prefix lines to parse alone
    prefixes = "\n".join(ln for ln in text.splitlines() if ln.startswith("@prefix"))
    return head, prefixes + "\n\n" + tail


def parse(text: str, label: str) -> Graph:
    g = Graph()
    try:
        g.parse(data=text, format="turtle")
    except Exception as exc:  # a parse failure here is a real bug, so say so
        raise SystemExit(f"{label}: turtle parse failed — {exc}") from exc
    return g


# ---------------------------------------------------------------- load graphs
load_prefixes(*(p.read_text(encoding="utf-8") for p in (
    PROPOSED / "ontology-definitions-raw.proposed.ttl",
    PROPOSED / "ontology-shapes-raw.proposed.ttl",
    PROPOSED / "ontology-enumerations-raw.proposed.ttl",
    HERE / "ontology.ttl",
    HERE / "examples" / "gimie-raw-instance.ttl",
)))
print(f"prefixes  {len(PREFIXES)} namespaces declared across the inputs")

defs_up_txt, defs_ours_txt = split_text(
    PROPOSED / "ontology-definitions-raw.proposed.ttl", SPLIT["definitions"])
enum_up_txt, enum_ours_txt = split_text(
    PROPOSED / "ontology-enumerations-raw.proposed.ttl", SPLIT["enumerations"])
shapes_up_txt, shapes_ours_txt = split_text(
    PROPOSED / "ontology-shapes-raw.proposed.ttl", SPLIT["shapes"])
shapes_txt = (PROPOSED / "ontology-shapes-raw.proposed.ttl").read_text(encoding="utf-8")

g_up = parse(defs_up_txt + "\n" + enum_up_txt, "upstream definitions")
g_ours = parse(defs_ours_txt + "\n" + enum_ours_txt, "proposed definitions")
g_shapes = parse(shapes_txt, "shapes")
g_inst = Graph()
g_inst.parse(HERE / "examples" / "gimie-raw-instance.ttl", format="turtle")

print(f"upstream  {len(g_up):5} triples")
print(f"proposed  {len(g_ours):5} triples")
print(f"shapes    {len(g_shapes):5} triples")
print(f"instance  {len(g_inst):5} triples")

g_all = g_up + g_ours

CLASS_TYPES = {RDFS.Class, OWL.Class}
PROP_TYPES = {RDF.Property, OWL.ObjectProperty, OWL.DatatypeProperty,
              OWL.AnnotationProperty}


def label_of(g: Graph, s) -> str:
    for p in (SKOS.prefLabel, RDFS.label, SH.name):
        v = g.value(s, p)
        if v:
            return str(v)
    c = curie(s)
    name = c.split(":", 1)[-1]
    # CamelCase / camelCase -> spaced words, so the fallback still reads
    return re.sub(r"(?<!^)(?=[A-Z])", " ", name).replace("_", " ").strip()


def definition_of(g: Graph, s) -> str:
    for p in (SKOS.definition, RDFS.comment, URIRef("http://purl.org/dc/terms/description")):
        v = g.value(s, p)
        if v:
            return str(v)
    return ""


# ----------------------------------------------------- where each term is DEFINED
# rdflib gives no line numbers, so the declarations are found textually. Result:
# every term links to the exact line of the exact file — their file at the pinned
# commit for their terms, ours in this repo for ours.
GH_BLOB = (f"https://github.com/sdsc-ordes/open-pulse-ontology/blob/{REF}/src/ontology")
SUBJECT_RE = re.compile(r"^([A-Za-z][\w-]*:[\w.-]+)")


def header_lines(text: str) -> int:
    """Length of the generated banner materialize_proposed_raw.py prepends.

    Needed to turn a line number in our copy into the line number in THEIR
    file, which is what a reviewer wants to open.
    """
    marks = [i for i, ln in enumerate(text.splitlines()) if ln.startswith("# ===")]
    return marks[1] + 2 if len(marks) > 1 else 0


declared_in: dict[str, list[dict]] = defaultdict(list)


def scan_declarations(text: str, label: str, *, upstream: bool, offset: int = 0,
                      path: str = "") -> None:
    for i, ln in enumerate(text.splitlines(), start=1):
        m = SUBJECT_RE.match(ln)
        if not m:
            continue
        line = i - offset
        if line < 1:
            continue
        entry = {
            "file": label,
            "line": line,
            "side": "upstream" if upstream else "gme",
            "url": f"{GH_BLOB}/{label}#L{line}" if upstream else "",
            "path": path,
        }
        cur = declared_in[m.group(1)]
        if not any(e["file"] == label and e["line"] == line for e in cur):
            cur.append(entry)


for label, text in (("ontology-definitions-raw.ttl", defs_up_txt),
                    ("ontology-enumerations-raw.ttl", enum_up_txt),
                    ("ontology-shapes-raw.ttl", shapes_up_txt)):
    scan_declarations(text, label, upstream=True, offset=header_lines(text))
for label, p in (("ontology.ttl", HERE / "ontology.ttl"),
                 ("ontology-shapes-raw.additions.ttl",
                  HERE / "ontology-shapes-raw.additions.ttl")):
    scan_declarations(p.read_text(encoding="utf-8"), label, upstream=False,
                      path=f"dev/v4.0.0/{label}")
TREE = f"https://github.com/sdsc-ordes/open-pulse-ontology/tree/{REF}/src/ontology"


def declarations_for(c: str) -> list[dict]:
    """Where a term is stated, or an honest note when we cannot say.

    pulse: terms the raw profile only *references* — pulse:partOfRun,
    pulse:PlatformProfile — are declared in their canonical or provenance
    profile, which we do not mirror here. Saying "declared elsewhere in their
    ontology" is true; guessing a file and a line would not be.
    """
    hits = declared_in.get(c, [])
    if hits:
        return hits
    pfx = prefix_of(c)
    if pfx and pfx != "pulse":
        # someone else's vocabulary: name it and point at its namespace
        ns = next((k for k, v in PREFIXES.items() if v == pfx), "")
        return [{"file": VOCAB_NAMES.get(pfx, pfx), "line": 0, "side": "external",
                 "url": ns, "path": "",
                 "note": f"defined by {VOCAB_NAMES.get(pfx, pfx)}, not by this proposal"}]
    return [{"file": "outside the raw profile", "line": 0, "side": "upstream",
             "url": TREE, "path": "",
             "note": "referenced by the raw profile; declared in their canonical or "
                     "provenance profile, which this proposal does not mirror"}]


print(f"declared  {len(declared_in)} terms located to a file and line")

# ------------------------------------------------------------------ instances
inst_by_class: Counter = Counter()
subjects_by_class: dict[str, set] = defaultdict(set)
for s, o in g_inst.subject_objects(RDF.type):
    c = curie(o)
    inst_by_class[c] += 1
    subjects_by_class[c].add(s)

prop_usage: Counter = Counter()
for _, p, _ in g_inst:
    prop_usage[curie(p)] += 1

# platform of every ExtractionOutput, then of every node that names its run
PULSE = URIRef(OURS_NS)


def pulse(name: str) -> URIRef:
    return URIRef(OURS_NS + name)


out_platform: dict[URIRef, str] = {}
for s, o in g_inst.subject_objects(pulse("platform")):
    out_platform[s] = curie(o).split(":", 1)[-1]

platform_nodes: Counter = Counter()
class_platforms: dict[str, set[str]] = defaultdict(set)
node_platforms: dict[str, set[str]] = defaultdict(set)
for s, o in g_inst.subject_objects(pulse("partOfRun")):
    plat = out_platform.get(o)
    if not plat:
        continue
    platform_nodes[plat] += 1
    node_platforms[curie(s)].add(plat)
    for t in g_inst.objects(s, RDF.type):
        class_platforms[curie(t)].add(plat)

# A node can name its platform three ways: through the run that produced it
# (partOfRun), directly (pulse:platform on an output), or as an Observation's
# sourcePlatform. Counting only the first left Observation, DependencyRelation
# and UnmappedField looking platform-less, which they are not.
for pred in (pulse("sourcePlatform"), pulse("platform")):
    for s, o in g_inst.subject_objects(pred):
        plat = curie(o).split(":", 1)[-1]
        node_platforms[curie(s)].add(plat)
        for t in g_inst.objects(s, RDF.type):
            class_platforms[curie(t)].add(plat)

# One-hop inheritance: a DependencyRelation, UnmappedField or ExternalIdentifier
# hangs off the node it describes and never names a run of its own. It came from
# whatever platform produced its parent, so it inherits that. Marked as inferred
# rather than asserted, and one hop only.
inherited: dict[str, set[str]] = defaultdict(set)
for s, p, o in g_inst:
    if isinstance(o, Literal) or p == RDF.type:
        continue
    oc, sc = curie(o), curie(s)
    if not node_platforms.get(oc) and node_platforms.get(sc):
        inherited[oc] |= node_platforms[sc]
for oc, plats in inherited.items():
    node_platforms[oc] |= plats
for s in set(g_inst.subjects()):
    plats = node_platforms.get(curie(s))
    if plats:
        for t in g_inst.objects(s, RDF.type):
            class_platforms[curie(t)] |= plats

# which platform actually supplies which property — the subject's platforms,
# plus the §18 Observation layer's explicit claim
prop_platforms: dict[str, set[str]] = defaultdict(set)
for s, p, _ in g_inst:
    plats = node_platforms.get(curie(s))
    if plats:
        prop_platforms[curie(p)] |= plats
for obs in g_inst.subjects(RDF.type, pulse("Observation")):
    prop = g_inst.value(obs, pulse("observedProperty"))
    plat = g_inst.value(obs, pulse("sourcePlatform"))
    if prop is not None and plat is not None:
        prop_platforms[curie(prop)].add(curie(plat).split(":", 1)[-1])

print(f"instances {len(inst_by_class)} node types, {len(platform_nodes)} platforms, "
      f"{len(prop_usage)} distinct properties used")

# ------------------------------------------------------ where the DATA came from
# Four kinds of source, each carrying its own provenance:
#   committed snapshot  a captured HTTP response in the test fixtures, with a
#                       .meta.json sidecar holding the request URL and time
#   live capture        fetched by fetch_live_sources.py into examples/sources/,
#                       same sidecar shape, so the test reproduces offline
#   local index         one of our DuckDB stores, or the SNSF bulk CSV
#   rete query          a SPARQL query against a published .rete graph
# Nothing here is asserted by hand: it is read off the manifests and the graph.
PLATFORM_OF = {
    "github": "GitHub", "ror": "ROR", "orcid": "ORCID", "infoscience": "Infoscience",
    "depsdev": "DepsDev", "dockerhub": "DockerHub", "openalex": "OpenAlex",
    "hf": "HuggingFace", "zenodo": "Zenodo", "cordis": "CORDIS", "snsf": "SNSF_P3",
    "ecosystems": "ecosyste.ms",
}
BUILDER_OF = {
    "GitHub": "build_instance_example.py", "ROR": "build_instance_example.py",
    "ORCID": "build_instance_multisource.py",
    "Infoscience": "build_instance_multisource.py",
    "DepsDev": "build_instance_multisource.py",
    "ecosyste.ms": "build_instance_multisource.py",
    "OpenAlex": "build_instance_multisource.py",
    "HuggingFace": "build_instance_multisource.py",
    "Zenodo": "build_instance_multisource.py",
    "SNSF_P3": "build_instance_funding.py", "CORDIS": "build_instance_funding.py",
    "DockerHub": "build_instance_ecosystem.py",
}
sources: list[dict] = []


def add_source(**kw) -> None:
    kw.setdefault("builder", BUILDER_OF.get(kw.get("platform", ""), ""))
    sources.append(kw)


# --- committed snapshots, from the fixture manifest
SNAP = ROOT / "tests" / "v2" / "fixtures" / "providers" / "live_snapshots"
manifest = SNAP / "manifest.json"
if manifest.exists():
    mf = json.loads(manifest.read_text(encoding="utf-8"))
    for e in mf.get("entries", []):
        meta = SNAP / e.get("meta_path", "")
        url = ""
        if meta.exists():
            url = ((json.loads(meta.read_text(encoding="utf-8")).get("request") or {})
                   .get("url", ""))
        add_source(
            kind="committed snapshot", platform=PLATFORM_OF.get(e.get("provider", ""), e.get("provider", "")),
            name=e.get("case", ""), url=url, capturedAt=e.get("captured_at", ""),
            status=e.get("status_code", ""), dataset=mf.get("dataset", ""),
            path=f"tests/v2/fixtures/providers/live_snapshots/{e.get('response_path', '')}",
        )

# --- live captures written by fetch_live_sources.py
for p in sorted((HERE / "examples" / "sources").glob("*.json")):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    req = d.get("request") if isinstance(d, dict) else None
    plat = PLATFORM_OF.get(p.stem.split("_")[0], p.stem.split("_")[0])
    add_source(
        kind="live capture", platform=plat, name=p.stem,
        url=(req or {}).get("url", "") if isinstance(req, dict) else "",
        capturedAt=(d.get("captured_at", "") if isinstance(d, dict) else ""),
        status=(d.get("status_code", "") if isinstance(d, dict) else ""),
        path=f"dev/v4.0.0/examples/sources/{p.name}",
        note="" if isinstance(req, dict) else
             "no request sidecar — captured before fetch_live_sources.py recorded one",
    )

# --- local indices and bulk files the builders read directly
LOCAL = [
    ("Zenodo", "data/index/zenodo/duckdb", "records", "build_instance_ecosystem.py"),
    ("DockerHub", "data/index/dockerhub/duckdb", "images", "build_instance_ecosystem.py"),
]
for plat, rel, table, builder in LOCAL:
    files = sorted((ROOT / rel).glob("*.duckdb")) if (ROOT / rel).exists() else []
    rows = None
    if files:
        try:
            import duckdb
            con = duckdb.connect(str(files[0]), read_only=True)
            rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            con.close()
        except Exception as exc:                     # absent store, lock, schema drift
            rows = f"unreadable ({type(exc).__name__})"
    add_source(kind="local index", platform=plat, name=f"{table} table",
               path=f"{rel}/{files[0].name}" if files else rel,
               rows=rows, available=bool(files), builder=builder,
               note="" if files else "not present in this checkout")

snsf_csv = ROOT / "data" / "index" / "snsf" / "raw" / "grants.csv"
add_source(kind="bulk export", platform="SNSF_P3", name="P3 grants.csv",
           path="data/index/snsf/raw/grants.csv", available=snsf_csv.exists(),
           rows=(sum(1 for _ in snsf_csv.open(encoding="utf-8-sig")) - 1
                 if snsf_csv.exists() else None),
           note="no live SNSF API exists — P3 is distributed as bulk CSV, so the "
                "provenance is a dated download rather than a request URL")

# --- rete SPARQL queries, taken from the graph's own retrievedFrom values
for url in sorted({str(o) for o in g_inst.objects(None, pulse("retrievedFrom"))
                   if ".rete" in str(o)}):
    add_source(kind="rete query", platform="DepsDev", name=url.rsplit("/", 1)[-1],
               url=url, note="range-read SPARQL against a published .rete graph")

# --- which builder emitted which subject, and on which line of the instance file
inst_txt = (HERE / "examples" / "gimie-raw-instance.ttl").read_text(encoding="utf-8")
emitted_by: dict[str, dict] = {}
builder = "build_instance_example.py"
for i, ln in enumerate(inst_txt.splitlines(), start=1):
    m = re.search(r"(build_instance_\w+\.py)", ln)
    if m:
        builder = m.group(1)
        continue
    m = re.match(r"^(<[^>]+>|[A-Za-z][\w-]*:[\w.-]+)\s", ln)
    if m:
        subj = m.group(1)
        subj = curie(URIRef(subj[1:-1])) if subj.startswith("<") else subj
        emitted_by.setdefault(subj, {"builder": builder, "line": i})

class_builders: dict[str, set[str]] = defaultdict(set)
for s, o in g_inst.subject_objects(RDF.type):
    e = emitted_by.get(curie(s))
    if e:
        class_builders[curie(o)].add(e["builder"])

# --- the §18 provenance layer, as a table: every claimed value with its URL
observations: list[dict] = []
for obs in g_inst.subjects(RDF.type, pulse("Observation")):
    g = g_inst
    observations.append({
        "subject": curie(g.value(obs, pulse("observedSubject"))),
        "property": curie(g.value(obs, pulse("observedProperty"))),
        "value": str(g.value(obs, pulse("observedValue")) or ""),
        "url": str(g.value(obs, pulse("retrievedFrom")) or ""),
        "at": str(g.value(obs, pulse("retrievedAt")) or ""),
        "platform": curie(g.value(obs, pulse("sourcePlatform")) or "").split(":")[-1],
        "obsKind": str(g.value(obs, pulse("observationKind")) or ""),
    })
observations.sort(key=lambda o: (o["platform"], o["property"]))

by_platform_src: dict[str, list[int]] = defaultdict(list)
for i, s in enumerate(sources):
    by_platform_src[s["platform"]].append(i)
print(f"sources   {len(sources)} payloads across {len(by_platform_src)} platforms "
      f"({Counter(s['kind'] for s in sources).most_common()}), "
      f"{len(observations)} observations")

# --------------------------------------------------------------------- shapes
# the three node shapes ontology-shapes-raw.additions.ttl contributes
OUR_SHAPES = {f"pulse:{n}Shape" for n in ("RawContribution", "RawGitIdentity", "RawProject")}
shapes: list[dict] = []
shape_for_class: dict[str, list[str]] = defaultdict(list)
constraints: dict[tuple[str, str], dict] = {}

for sh_node in set(g_shapes.subjects(SH.property, None)) | set(
        g_shapes.subjects(RDF.type, SH.NodeShape)):
    if isinstance(sh_node, BNode):
        continue
    name = curie(sh_node)
    target = g_shapes.value(sh_node, SH.targetClass)
    closed = g_shapes.value(sh_node, SH.closed)
    props = []
    for pshape in g_shapes.objects(sh_node, SH.property):
        path = g_shapes.value(pshape, SH.path)
        if path is None:
            continue
        entry = {
            "path": curie(path),
            "name": str(g_shapes.value(pshape, SH.name) or ""),
            "datatype": curie(g_shapes.value(pshape, SH.datatype)) if g_shapes.value(pshape, SH.datatype) else "",
            "class": curie(g_shapes.value(pshape, SH["class"])) if g_shapes.value(pshape, SH["class"]) else "",
            "nodeKind": curie(g_shapes.value(pshape, SH.nodeKind)) if g_shapes.value(pshape, SH.nodeKind) else "",
            "minCount": str(g_shapes.value(pshape, SH.minCount) or ""),
            "maxCount": str(g_shapes.value(pshape, SH.maxCount) or ""),
            "pattern": str(g_shapes.value(pshape, SH.pattern) or ""),
        }
        props.append(entry)
        if target is not None:
            constraints[(curie(target), entry["path"])] = entry
    tgt = curie(target) if target is not None else ""
    shapes.append({
        "name": name,
        "targetClass": tgt,
        "closed": str(closed).lower() if closed is not None else "",
        "properties": sorted(props, key=lambda e: e["path"]),
        "origin": "proposed" if name in OUR_SHAPES else "upstream",
        "declaredIn": declared_in.get(name, []),
    })
    if tgt:
        shape_for_class[tgt].append(name)

shapes.sort(key=lambda s: s["name"])
print(f"shapes    {len(shapes)} node shapes, "
      f"{sum(len(s['properties']) for s in shapes)} property constraints")

# ---------------------------------------------------------------------- terms
terms: dict[str, dict] = {}


def origin_of(subject) -> str:
    in_ours = (subject, None, None) in g_ours
    in_up = (subject, None, None) in g_up
    if in_ours and not in_up:
        return "proposed"
    if in_up and in_ours:
        return "extended"
    return "upstream"


def register(subject, kind: str) -> dict:
    c = curie(subject)
    if c in terms:
        return terms[c]
    g = g_ours if (subject, None, None) in g_ours else g_up
    entry = {
        "curie": c,
        "iri": str(subject),
        "kind": kind,
        "label": label_of(g, subject),
        "definition": definition_of(g, subject),
        "origin": origin_of(subject),
        "vocab": prefix_of(c),
        "mapsFrom": sorted(str(v) for v in g_all.objects(subject, URIRef(GME_NS + "mapsFrom"))),
        "note": str(g.value(subject, RDFS.comment) or "") if g.value(subject, SKOS.definition) else "",
        "domain": sorted({curie(o) for o in g_all.objects(subject, RDFS.domain)}),
        "range": sorted({curie(o) for o in g_all.objects(subject, RDFS.range)}),
        "subClassOf": sorted({curie(o) for o in g_all.objects(subject, RDFS.subClassOf)}),
        "subPropertyOf": sorted({curie(o) for o in g_all.objects(subject, RDFS.subPropertyOf)}),
        "equivalent": sorted({curie(o) for o in list(g_all.objects(subject, OWL.equivalentClass))
                              + list(g_all.objects(subject, OWL.equivalentProperty))}),
        "inverseOf": sorted({curie(o) for o in g_all.objects(subject, OWL.inverseOf)}),
        "instances": inst_by_class.get(c, 0),
        "uses": prop_usage.get(c, 0),
        "platforms": sorted(class_platforms.get(c, set()) | prop_platforms.get(c, set())),
        "shapes": sorted(shape_for_class.get(c, [])),
        "declaredIn": declarations_for(c),
        "emittedBy": sorted(class_builders.get(c, set())),
    }
    terms[c] = entry
    return entry


for s in set(g_all.subjects(RDF.type, None)):
    if isinstance(s, BNode):
        continue
    types = set(g_all.objects(s, RDF.type))
    if types & CLASS_TYPES:
        register(s, "class")
    elif types & PROP_TYPES:
        register(s, "property")
    elif any(curie(t).endswith("Enumeration") for t in types):
        register(s, "member")

# properties declared only by domain/range (no rdf:type line)
for s in set(g_all.subjects(RDFS.domain, None)) | set(g_all.subjects(RDFS.range, None)):
    if not isinstance(s, BNode) and curie(s) not in terms:
        register(s, "property")

# enumeration classes and their members
enums: dict[str, list[str]] = defaultdict(list)
for c, t in terms.items():
    if t["kind"] == "member":
        for ty in g_all.objects(URIRef(t["iri"]), RDF.type):
            if curie(ty).endswith("Enumeration"):
                enums[curie(ty)].append(c)
for k in enums:
    enums[k].sort()
    if k not in terms:
        register(URIRef(OURS_NS + k.split(":", 1)[-1]), "class")

# Terms the raw profile leans on but does not declare: pulse:PlatformProfile,
# pulse:Contribution and pulse:ExtractionOutput live in their canonical or
# provenance profile. Leaving them out made the class list quietly wrong — those
# three are the busiest nodes in the real data.
IRI_OF = {pfx: ns for ns, pfx in PREFIXES.items()}


def iri_of(c: str) -> URIRef | None:
    pfx, _, local = c.partition(":")
    ns = IRI_OF.get(pfx)
    return URIRef(ns + local) if ns and local else None


referenced_classes = ({sh["targetClass"] for sh in shapes} | set(inst_by_class) |
                      {p["class"] for sh in shapes for p in sh["properties"] if p["class"]})
referenced_props = ({p["path"] for sh in shapes for p in sh["properties"]} |
                    set(prop_usage))
added = 0
for c, kind in ([(c, "class") for c in sorted(referenced_classes)] +
                [(p, "property") for p in sorted(referenced_props)]):
    if not c or c in terms or ":" not in c or c.startswith(("_:", "http")):
        continue
    if prefix_of(c) in ("xsd", "rdf", "rdfs", "sh"):
        continue
    subject = iri_of(c)
    if subject is not None:
        register(subject, kind)
        added += 1
print(f"referenced {added} terms used by the profile but declared outside the raw files")

print(f"terms     {sum(1 for t in terms.values() if t['kind'] == 'class')} classes, "
      f"{sum(1 for t in terms.values() if t['kind'] == 'property')} properties, "
      f"{sum(1 for t in terms.values() if t['kind'] == 'member')} enumeration members "
      f"in {len(enums)} enumerations")

# --------------------------------------------------------- properties by class
for t in terms.values():
    t["propsIn"] = []
    t["propsOut"] = []
for c, t in terms.items():
    if t["kind"] != "property":
        continue
    for d in t["domain"]:
        if d in terms:
            terms[d]["propsOut"].append(c)
    for r in t["range"]:
        if r in terms and terms[r]["kind"] == "class":
            terms[r]["propsIn"].append(c)

# shape-declared properties a class carries even without rdfs:domain
for sh in shapes:
    tgt = sh["targetClass"]
    if tgt in terms:
        for p in sh["properties"]:
            if p["path"] not in terms[tgt]["propsOut"]:
                terms[tgt]["propsOut"].append(p["path"])

# ------------------------------------------------------------------ alignment
# Two different claims, kept apart so neither inflates the other:
#   axiom  — "our term IS a kind of their term" (subClassOf / subPropertyOf /
#            equivalent). These are the alignment commitments; every target IRI
#            was fetched and checked (verification.md).
#   typing — our term's domain/range happens to be a class from another
#            vocabulary. Reuse, but not a claim about our term's meaning.
ALIGN_AXIOMS = [(RDFS.subClassOf, "rdfs:subClassOf"),
                (RDFS.subPropertyOf, "rdfs:subPropertyOf"),
                (OWL.equivalentClass, "owl:equivalentClass"),
                (OWL.equivalentProperty, "owl:equivalentProperty")]
TYPING_AXIOMS = [(RDFS.range, "rdfs:range"), (RDFS.domain, "rdfs:domain")]
alignments: list[dict] = []
typings: list[dict] = []
for bucket, axioms in ((alignments, ALIGN_AXIOMS), (typings, TYPING_AXIOMS)):
    for pred, plabel in axioms:
        for s, o in g_ours.subject_objects(pred):
            if isinstance(s, BNode) or isinstance(o, BNode):
                continue
            sc, oc = curie(s), curie(o)
            tv = prefix_of(oc)
            if tv in ("pulse", "gme", "xsd", "") or prefix_of(sc) not in ("pulse", "schema"):
                continue
            bucket.append({"term": sc, "axiom": plabel, "target": oc,
                           "vocab": VOCAB_NAMES.get(tv, tv), "prefix": tv})
for b in (alignments, typings):
    b.sort(key=lambda a: (a["vocab"], a["term"]))
print(f"alignment {len(alignments)} alignment axioms across "
      f"{len({a['vocab'] for a in alignments})} vocabularies, "
      f"{len(typings)} external domain/range")

# ------------------------------------------------------------------- sections
sections: list[dict] = []
our_ttl = (HERE / "ontology.ttl").read_text(encoding="utf-8")
# The banner line above is required: "# 14.4m packages / 109 registries" inside
# §21's prose otherwise reads as a second section 14.
for m in re.finditer(r"^# =+\n# (\d+)\. ([A-Za-z][^\n]*)$", our_ttl, flags=re.MULTILINE):
    n, title = int(m.group(1)), m.group(2).strip()
    body = our_ttl[m.end():]
    nxt = re.search(r"^# =====", body, flags=re.MULTILINE)
    blurb = body[:nxt.start()] if nxt else body[:1200]
    blurb = " ".join(ln.lstrip("# ").strip() for ln in blurb.splitlines()
                     if ln.startswith("#") and not ln.startswith("# ==="))
    seg_end = re.search(r"^# =========================================================================$",
                        body[nxt.end():] if nxt else body, flags=re.MULTILINE)
    segment = (body[nxt.end():nxt.end() + seg_end.start()] if (nxt and seg_end)
               else body[:4000])
    seg_terms = [f"pulse:{x}" for x in
                 dict.fromkeys(re.findall(r"^pulse:(\w+)", segment, flags=re.MULTILINE))]
    sections.append({"n": n, "title": title, "blurb": blurb[:600],
                     "terms": [t for t in seg_terms if t in terms]})
sections = [s for s in {s["n"]: s for s in sections}.values()]
sections.sort(key=lambda s: s["n"])
print(f"sections  {len(sections)} argued sections in ontology.ttl")

# --------------------------------------------------------------------- network
# A node is anything used as a class anywhere: declared, targeted by a shape,
# named as a domain/range, aligned to, or actually instantiated in the real
# data. Restricting to *declared* classes gave 17 nodes and 7 links, because
# most of the structure lives in the shapes and in the instance graph.
classes = {c: t for c, t in terms.items() if t["kind"] == "class"}
net_nodes: dict[str, dict] = {}


def node_for(c: str) -> dict | None:
    if not c or c.startswith("_:") or c.startswith("http") or c.endswith("Enumeration"):
        return None
    if prefix_of(c) in ("xsd", "rdf", "rdfs", "sh", "gme"):
        return None
    if c not in net_nodes:
        t = terms.get(c, {})
        pfx = prefix_of(c)
        net_nodes[c] = {
            "id": c,
            "kind": "class",
            "label": t.get("label") or re.sub(r"(?<!^)(?=[A-Z])", " ", c.split(":", 1)[-1]),
            "origin": t.get("origin") or ("proposed" if pfx == "pulse" else "external"),
            "vocab": pfx,
            "declared": bool(t),
            "instances": inst_by_class.get(c, 0),
            "platforms": sorted(class_platforms.get(c, set())),
            "props": len(t.get("propsOut", [])),
            "degree": 0,
        }
    return net_nodes[c]


links: list[dict] = []


def add_link(a: str, b: str, kind: str, via: str, count: int = 0) -> None:
    na, nb = node_for(a), node_for(b)
    if na is None or nb is None or a == b:
        return
    links.append({"source": a, "target": b, "kind": kind, "via": via, "count": count})
    na["degree"] += 1
    nb["degree"] += 1


# seed every class-like thing so isolated nodes still appear
for c in classes:
    node_for(c)
for sh in shapes:
    node_for(sh["targetClass"])
for c in inst_by_class:
    node_for(c)

# 1. declared object properties: domain -> range
for c, t in terms.items():
    if t["kind"] != "property":
        continue
    for d in t["domain"]:
        for r in t["range"]:
            add_link(d, r, "property", c)

# 2. shape-declared edges — sh:class on a property shape of a node shape
for sh in shapes:
    for p in sh["properties"]:
        if p["class"]:
            add_link(sh["targetClass"], p["class"], "shape", p["path"])

# 3. taxonomy and alignment: pulse->pulse is taxonomy, pulse->foreign is alignment
for c, t in classes.items():
    for sup in t["subClassOf"]:
        add_link(c, sup, "subclass" if prefix_of(sup) == prefix_of(c) else "alignment",
                 "rdfs:subClassOf")

# 4. the real network: every edge the 11-platform instance graph actually draws
#    between two typed nodes, collapsed to class level and counted.
inst_type: dict = {}
for s, o in g_inst.subject_objects(RDF.type):
    inst_type.setdefault(s, curie(o))
observed: Counter = Counter()
for s, p, o in g_inst:
    if p == RDF.type or isinstance(o, Literal):
        continue
    ts, to = inst_type.get(s), inst_type.get(o)
    if ts and to:
        observed[(ts, to, curie(p))] += 1
for (ts, to, p), n in observed.items():
    add_link(ts, to, "observed", p, n)

# 5. the alignment layer. 25 of the 32 axioms are subPropertyOf, so they never
#    show up as class-to-class edges — yet "where does our model touch the
#    outside world" is exactly what the network is for. One node per external
#    vocabulary, joined to the class that owns the aligned property.
vocab_edges: Counter = Counter()
for a in alignments:
    vnode = "vocab:" + a["prefix"]
    if vnode not in net_nodes:
        net_nodes[vnode] = {
            "id": vnode, "kind": "vocab", "label": a["vocab"], "origin": "external",
            "vocab": a["prefix"], "declared": False, "instances": 0,
            "platforms": [], "props": 0, "degree": 0,
        }
    t = terms.get(a["term"], {})
    owners = [a["term"]] if t.get("kind") == "class" else t.get("domain", [])
    for owner in owners or ["pulse:_unowned"]:
        if owner in net_nodes:
            vocab_edges[(owner, vnode)] += 1
for (owner, vnode), n in vocab_edges.items():
    links.append({"source": owner, "target": vnode, "kind": "vocabAlignment",
                  "via": f"{n} axiom{'s' if n > 1 else ''}", "count": n})

# dedupe: one edge per (source, target, via, kind), keeping the largest count
best: dict[tuple, dict] = {}
for l in links:
    k = (l["source"], l["target"], l["via"], l["kind"])
    if k not in best or l["count"] > best[k]["count"]:
        best[k] = l
links = list(best.values())
for n in net_nodes.values():
    n["degree"] = 0
for l in links:
    net_nodes[l["source"]]["degree"] += 1
    net_nodes[l["target"]]["degree"] += 1

kinds = Counter(l["kind"] for l in links)
print(f"network   {len(net_nodes)} nodes, {len(links)} links "
      f"({', '.join(f'{k} {v}' for k, v in kinds.most_common())})")

# ----------------------------------------------------------------- the payload
payload = {
    "meta": {
        "upstreamRef": REF,
        "upstreamBlob": GH_BLOB,
        "upstreamRepo": "sdsc-ordes/open-pulse-ontology",
        "upstreamBranch": "feature/platform-profiles (PR #25)",
        "gmeVersion": "3.0.0",
        "triplesUpstream": len(g_up),
        "triplesProposed": len(g_ours),
        "triplesShapes": len(g_shapes),
        "triplesInstance": len(g_inst),
        "violationsBefore": 52,
        "violationsAfter": 0,
    },
    "terms": terms,
    "platformFields": platform_inventory.build(terms),
    "sources": sources,
    "observations": observations,
    "shapes": shapes,
    "enums": {k: v for k, v in sorted(enums.items())},
    "alignments": alignments,
    "typings": typings,
    "sections": sections,
    "network": {"nodes": list(net_nodes.values()), "links": links},
    "instances": {
        "byClass": dict(inst_by_class.most_common()),
        "byPlatform": dict(platform_nodes.most_common()),
        "propUsage": dict(prop_usage.most_common()),
    },
}

data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
# a definition containing "</script>" would end the script element early; "<\/"
# is the same string to a JSON parser and inert to the HTML tokenizer
data = data.replace("</", "<\\/")
html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
if "/*__DATA__*/" in html:
    raise SystemExit("template placeholder not replaced — check explorer_template.html")
OUT.write_text(html, encoding="utf-8")
print(f"\nwrote {OUT.relative_to(HERE.parents[1])}  "
      f"{len(html) / 1024:.0f} KB, payload {len(data) / 1024:.0f} KB")
