#!/usr/bin/env python3
"""Build the Open Pulse Ontology v3.0.0 (proposed) documentation.

Produces, under docs/releases/v3.0.0/:
  * open-pulse-ontology-v3.0.0.ttl  — the merged, reproducible TTL source
        (v2.1.2 carried forward + all v3 proposed additions + the GME internal
        vocabulary, inline), and
  * index.html                      — a self-contained, pyLODE-style HTML
        reference that documents every class, property and named individual,
        including the internal (gme-internal:) provider fields.

Sources (single source of truth — edit those, not the generated files):
  * git_metadata_extractor/validation/open-pulse-ontology-v2.1.2.ttl   (current release)
  * .internal/ontology-v3/07-ttl-draft.md              (proposed v3 additions)
  * docs/gme-internal.ttl                              (internal vocabulary)

Run:  python3 scripts/build_ontology_v3_docs.py
"""
from __future__ import annotations

import re
from pathlib import Path
from html import escape

from rdflib import Graph, RDF, RDFS, OWL, URIRef, BNode, Literal
from rdflib.namespace import Namespace, SKOS, DCTERMS, XSD

ROOT = Path(__file__).resolve().parent.parent
V2_TTL = ROOT / "git_metadata_extractor/validation/open-pulse-ontology-v2.1.2.ttl"
V3_DRAFT = ROOT / ".internal/ontology-v3/07-ttl-draft.md"
GME_TTL = ROOT / "docs/gme-internal.ttl"
OUT_DIR = ROOT / "docs/releases/v3.0.0"
OUT_TTL = OUT_DIR / "open-pulse-ontology-v3.0.0.ttl"
OUT_HTML = OUT_DIR / "index.html"

SH = Namespace("http://www.w3.org/ns/shacl#")
PULSE = Namespace("https://open-pulse.epfl.ch/ontology#")
SCHEMA = Namespace("http://schema.org/")
ORG = Namespace("http://www.w3.org/ns/org#")
TIME = Namespace("http://www.w3.org/2006/time#")
PROV = Namespace("http://www.w3.org/ns/prov#")
GME = Namespace("https://openpulse.science/git-metadata-extractor#")
WD = Namespace("http://www.wikidata.org/entity/")

PREFIXES = [
    ("sh", "http://www.w3.org/ns/shacl#"),
    ("xsd", "http://www.w3.org/2001/XMLSchema#"),
    ("schema", "http://schema.org/"),
    ("rdfs", "http://www.w3.org/2000/01/rdf-schema#"),
    ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"),
    ("org", "http://www.w3.org/ns/org#"),
    ("time", "http://www.w3.org/2006/time#"),
    ("prov", "http://www.w3.org/ns/prov#"),
    ("pulse", "https://open-pulse.epfl.ch/ontology#"),
    ("owl", "http://www.w3.org/2002/07/owl#"),
    ("dct", "http://purl.org/dc/terms/"),
    ("wd", "http://www.wikidata.org/entity/"),
    ("skos", "http://www.w3.org/2004/02/skos/core#"),
    ("gme-internal", "https://openpulse.science/git-metadata-extractor#"),
]

# Domainless properties whose intended subject is known from the v3 design docs
# (the draft declares the range but omits rdfs:domain). Used only to anchor them
# in the graph / side panel — not written into the TTL.
SUPPLEMENT_DOMAIN = {
    "pulse:publicationType": "schema:ScholarlyArticle",
    "pulse:openAccessStatus": "schema:ScholarlyArticle",
    "pulse:citationCount": "schema:ScholarlyArticle",
    "pulse:grantId": "schema:ScholarlyArticle",
}

# namespace IRI -> (short prefix, display badge)
NS_BADGE = {
    "https://open-pulse.epfl.ch/ontology#": ("pulse", "Open Pulse"),
    "http://schema.org/": ("schema", "schema.org"),
    "http://www.w3.org/ns/org#": ("org", "ORG"),
    "http://www.w3.org/ns/prov#": ("prov", "PROV"),
    "http://www.w3.org/2006/time#": ("time", "TIME"),
    "https://openpulse.science/git-metadata-extractor#": ("gme-internal", "GME internal"),
    "http://www.wikidata.org/entity/": ("wd", "Wikidata"),
    "http://www.w3.org/2002/07/owl#": ("owl", "OWL"),
    "http://www.w3.org/2000/01/rdf-schema#": ("rdfs", "RDFS"),
    "http://www.w3.org/2001/XMLSchema#": ("xsd", "XSD"),
    "http://www.w3.org/2004/02/skos/core#": ("skos", "SKOS"),
}


# --------------------------------------------------------------------------- #
#  1. Assemble the merged TTL                                                  #
# --------------------------------------------------------------------------- #
def extract_turtle_blocks(markdown: str) -> str:
    """Return every ```turtle fenced block concatenated."""
    blocks = re.findall(r"```turtle\s*\n(.*?)```", markdown, flags=re.DOTALL)
    return "\n".join(blocks)


def strip_prefix_lines(ttl: str) -> str:
    return "\n".join(
        ln for ln in ttl.splitlines() if not ln.lstrip().startswith("@prefix")
    )


def normalize_literals(ttl: str) -> str:
    """Collapse newlines that appear *inside* quoted string literals.

    The v3 draft wraps some skos:definition strings across several lines inside
    a single-quoted literal, which is invalid Turtle. Walk the text, and while
    inside a (single- or triple-) quoted string, fold internal newlines + the
    following indentation into a single space. Comments are passed through.
    """
    out = []
    i, n = 0, len(ttl)
    while i < n:
        c = ttl[i]
        if c == "#":  # line comment — copy verbatim to end of line
            j = ttl.find("\n", i)
            j = n if j == -1 else j
            out.append(ttl[i:j])
            i = j
            continue
        if c in "\"'":
            if ttl[i : i + 3] == c * 3:  # triple-quoted: copy as-is (multiline ok)
                end = ttl.find(c * 3, i + 3)
                end = n if end == -1 else end + 3
                out.append(ttl[i:end])
                i = end
                continue
            # single-quoted literal — fold internal newlines
            out.append(c)
            i += 1
            while i < n:
                d = ttl[i]
                if d == "\\" and i + 1 < n:
                    out.append(ttl[i : i + 2])
                    i += 2
                    continue
                if d == c:
                    out.append(d)
                    i += 1
                    break
                if d in "\r\n":
                    out.append(" ")
                    i += 1
                    while i < n and ttl[i] in " \t\r\n":
                        i += 1
                    continue
                out.append(d)
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def build_merged_ttl() -> str:
    v2 = V2_TTL.read_text()
    # Drop v2's @prefix header and its v2.1.2 owl:Ontology declaration; the
    # v3 draft supplies the authoritative v3.0.0 ontology node.
    v2_body = strip_prefix_lines(v2)
    v2_body = re.sub(
        r"<https://open-pulse\.epfl\.ch/ontology#>\s+a\s+owl:Ontology\s*;.*?"
        r'owl:versionInfo\s+"v2\.1\.2"\^\^xsd:string\s*\.',
        "# (v2.1.2 owl:Ontology node replaced by the v3.0.0 declaration below)",
        v2_body,
        flags=re.DOTALL,
    )

    v3_body = strip_prefix_lines(extract_turtle_blocks(V3_DRAFT.read_text()))

    gme_body = strip_prefix_lines(GME_TTL.read_text())

    header = "\n".join(f"@prefix {p}: <{u}> ." for p, u in PREFIXES)

    deprecations = (
        "# --- v3 deprecation annotations (see migration notes 08) ---\n"
        "pulse:githubUsername owl:deprecated true ;\n"
        '    rdfs:comment "Deprecated in v3.0.0 — use pulse:githubLogin '
        '(current handle) and pulse:githubAccounts (all handles). '
        'Removed in v3.1.0." .\n"""'.replace('"""', "")
    )

    parts = [
        "# Open Pulse Ontology — v3.0.0 (PROPOSED / DRAFT)",
        "# =================================================",
        "# GENERATED FILE — do not edit by hand.",
        "# Reproduce with:  python3 scripts/build_ontology_v3_docs.py",
        "#",
        "# Merge of:",
        "#   * git_metadata_extractor/validation/open-pulse-ontology-v2.1.2.ttl  (current release)",
        "#   * .internal/ontology-v3/07-ttl-draft.md             (proposed v3 additions)",
        "#   * docs/gme-internal.ttl                             (internal vocabulary)",
        "#",
        "# The gme-internal:* terms are a SEPARATE, non-normative vocabulary; they are",
        "# emitted only with ?include_internal_fields=true and are intentionally NOT",
        "# conformant to the closed Open Pulse SHACL shapes.",
        "",
        header,
        "",
        "# ===========================================================================",
        "# OPEN PULSE ONTOLOGY (v2.1.2 terms carried forward + v3.0.0 additions)",
        "# ===========================================================================",
        v2_body,
        "",
        "# ---------------------------------------------------------------------------",
        "# v3.0.0 PROPOSED ADDITIONS",
        "# ---------------------------------------------------------------------------",
        v3_body,
        "",
        deprecations,
        "",
        "# ===========================================================================",
        "# GME INTERNAL VOCABULARY (non-normative, include_internal_fields=true)",
        "# ===========================================================================",
        gme_body,
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
#  2. HTML rendering helpers                                                   #
# --------------------------------------------------------------------------- #
def curie(g: Graph, uri) -> str:
    s = str(uri)
    for u, (p, _) in NS_BADGE.items():
        if s.startswith(u):
            return f"{p}:{s[len(u):]}"
    try:
        return g.namespace_manager.normalizeUri(uri)
    except Exception:
        return s


def local(uri) -> str:
    s = str(uri)
    for sep in ("#", "/"):
        if sep in s:
            return s.rsplit(sep, 1)[-1]
    return s


def anchor(uri) -> str:
    return local(uri)


def ns_prefix(uri) -> str:
    s = str(uri)
    for u, (p, _) in NS_BADGE.items():
        if s.startswith(u):
            return p
    return "other"


def badge(uri) -> str:
    s = str(uri)
    for u, (_, label) in NS_BADGE.items():
        if s.startswith(u):
            cls = "gme" if "git-metadata-extractor" in u else "pulse" if "open-pulse" in u else "ext"
            return f'<span class="ns-badge ns-{cls}">{escape(label)}</span>'
    return ""


def get_label(g: Graph, s) -> str:
    for pred in (SKOS.prefLabel, RDFS.label, SCHEMA.name):
        v = g.value(s, pred)
        if v:
            return str(v)
    return local(s)


def get_desc(g: Graph, s) -> str:
    for pred in (SKOS.definition, RDFS.comment, DCTERMS.description, SH.description):
        v = g.value(s, pred)
        if v:
            return re.sub(r"\s+", " ", str(v)).strip()
    return ""


def link(g: Graph, uri, known: set) -> str:
    c = curie(g, uri)
    if uri in known:
        return f'<a href="#{anchor(uri)}">{escape(c)}</a>'
    s = str(uri)
    if s.startswith("http"):
        return f'<a href="{escape(s)}" target="_blank" rel="noopener">{escape(c)}</a>'
    return escape(c)


def is_deprecated(g: Graph, s) -> bool:
    return (s, OWL.deprecated, Literal(True)) in g


# --------------------------------------------------------------------------- #
#  3. SHACL shape extraction                                                   #
# --------------------------------------------------------------------------- #
def shape_for_class(g: Graph, cls):
    """Return list of constraint dicts for the NodeShape targeting `cls`."""
    rows = []
    for shape in g.subjects(SH.targetClass, cls):
        for pshape in g.objects(shape, SH.property):
            path = g.value(pshape, SH.path)
            if path is None:
                continue
            rows.append(
                {
                    "path": path,
                    "name": g.value(pshape, SH.name),
                    "minCount": g.value(pshape, SH.minCount),
                    "maxCount": g.value(pshape, SH.maxCount),
                    "datatype": g.value(pshape, SH.datatype),
                    "class": g.value(pshape, SH["class"]),
                    "nodeKind": g.value(pshape, SH.nodeKind),
                    "pattern": g.value(pshape, SH.pattern),
                    "desc": g.value(pshape, SH.description),
                }
            )
        closed = g.value(shape, SH.closed)
        return rows, (str(closed) == "true")
    return None, None


def card(mn, mx) -> str:
    mn = int(mn) if mn is not None else None
    mx = int(mx) if mx is not None else None
    if mn is None and mx is None:
        return "0..*"
    if mn == 1 and mx == 1:
        return "exactly 1"
    if mn == 1 and mx is None:
        return "1..*"
    if (mn in (0, None)) and mx == 1:
        return "0..1"
    lo = mn if mn is not None else 0
    hi = mx if mx is not None else "∗"
    return f"{lo}..{hi}"


# --------------------------------------------------------------------------- #
#  4. Build the document model                                                 #
# --------------------------------------------------------------------------- #
def classify(g: Graph):
    enum_root = SCHEMA.Enumeration
    enum_classes = {enum_root}
    for c in g.subjects(RDFS.subClassOf, enum_root):
        enum_classes.add(c)

    all_class_subjects = set(g.subjects(RDF.type, RDFS.Class)) | set(
        g.subjects(RDF.type, OWL.Class)
    )

    classes = sorted(all_class_subjects, key=lambda s: curie(g, s).lower())

    # named individuals = instances of an enumeration class
    individuals = {}
    for ec in enum_classes:
        for ind in g.subjects(RDF.type, ec):
            if ind in all_class_subjects:
                continue
            individuals.setdefault(ec, []).append(ind)
    for ec in individuals:
        individuals[ec].sort(key=lambda s: get_label(g, s).lower())

    # properties
    prop_types = (RDF.Property, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty)
    prop_subjects = set()
    for pt in prop_types:
        prop_subjects |= set(g.subjects(RDF.type, pt))
    # drop SHACL shapes accidentally typed
    prop_subjects = {p for p in prop_subjects if not str(p).endswith("Shape")}

    obj_props, data_props = [], []
    for p in prop_subjects:
        rng = g.value(p, RDFS.range)
        is_data = False
        if rng is not None and str(rng).startswith(str(XSD)):
            is_data = True
        elif rng is None and str(p).startswith(str(GME)):
            is_data = True  # internal provider literals
        if is_data:
            data_props.append(p)
        else:
            obj_props.append(p)
    obj_props.sort(key=lambda s: curie(g, s).lower())
    data_props.sort(key=lambda s: curie(g, s).lower())

    return classes, obj_props, data_props, individuals, all_class_subjects, prop_subjects


def in_domain_of(g, cls, props):
    return sorted(
        [p for p in props if (p, RDFS.domain, cls) in g],
        key=lambda s: curie(g, s).lower(),
    )


def in_range_of(g, cls, props):
    return sorted(
        [p for p in props if (p, RDFS.range, cls) in g],
        key=lambda s: curie(g, s).lower(),
    )


# --------------------------------------------------------------------------- #
#  5. HTML emission                                                            #
# --------------------------------------------------------------------------- #
CSS = """
:root{--pulse:#1f6f8b;--gme:#9c4dcc;--ext:#7a7a7a;--gold:#b8860b;--blue:#2a6db0;--green:#2e8b57;--ni:#a0522d;}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;color:#1b1b1b;line-height:1.55;margin:0;background:#fff}
#wrap{max-width:1080px;margin:0 auto;padding:0 28px 96px}
header.site{background:linear-gradient(135deg,#13404f,#1f6f8b);color:#fff;padding:34px 28px;margin-bottom:8px}
header.site .inner{max-width:1080px;margin:0 auto}
header.site h1{margin:0 0 6px;font-size:1.9rem;font-weight:700}
header.site .sub{opacity:.9;font-size:1rem}
.banner{border-left:5px solid var(--gold);background:#fff8e6;padding:14px 18px;margin:22px 0;border-radius:4px;font-size:.94rem}
.banner.gme{border-left-color:var(--gme);background:#f7f0fb}
h2{font-size:1.5rem;border-bottom:2px solid #e2e2e2;padding-bottom:6px;margin-top:48px}
h3{font-size:1.05rem;margin:26px 0 4px}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%;margin:6px 0 18px;font-size:.9rem}
th,td{border:1px solid #dcdcdc;padding:7px 10px;text-align:left;vertical-align:top}
th{background:#f3f6f7;width:160px;font-weight:600;color:#33484f}
.toc{columns:2;column-gap:40px;font-size:.92rem;margin:14px 0 8px}
.toc a{display:block;padding:1px 0}
.meta-table th{width:200px}
.entity{border:1px solid #e6e6e6;border-radius:6px;padding:4px 18px 14px;margin:18px 0;background:#fcfcfd}
.entity h3{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
code,.mono{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:.85em;background:#f2f2f4;padding:1px 5px;border-radius:3px}
sup.t{font-size:.6em;font-weight:700;padding:1px 5px;border-radius:3px;color:#fff;vertical-align:super;text-transform:uppercase;letter-spacing:.04em}
sup.c{background:var(--gold)} sup.op{background:var(--blue)} sup.dp{background:var(--green)} sup.ni{background:var(--ni)}
.ns-badge{font-size:.62rem;font-weight:700;padding:2px 7px;border-radius:10px;text-transform:uppercase;letter-spacing:.04em;color:#fff;background:var(--ext)}
.ns-badge.ns-pulse{background:var(--pulse)} .ns-badge.ns-gme{background:var(--gme)}
.dep{background:#c0392b;color:#fff;font-size:.62rem;font-weight:700;padding:2px 7px;border-radius:10px;text-transform:uppercase}
.back{font-size:.78rem;margin-left:auto}
.shape-table th{background:#eef4ef;width:auto}
.shape-table td:first-child{white-space:nowrap}
.legend span{margin-right:18px;font-size:.86rem}
footer{margin-top:60px;padding-top:18px;border-top:1px solid #e2e2e2;font-size:.82rem;color:#666}
.pill{display:inline-block;background:#eef4f6;color:#33484f;border-radius:12px;padding:2px 10px;font-size:.78rem;margin:2px 4px 2px 0}
"""


def term_block(g, s, kind_tag, known, props_all):
    rows = []
    rows.append(("IRI", f'<code>{escape(str(s))}</code>'))
    desc = get_desc(g, s)
    if desc:
        rows.append(("Description", escape(desc)))

    def link_list(pred, reverse=False):
        if reverse:
            vals = sorted(g.subjects(pred, s), key=lambda x: curie(g, x).lower())
        else:
            vals = sorted(g.objects(s, pred), key=lambda x: curie(g, x).lower())
        return ", ".join(link(g, v, known) for v in vals) or ""

    # relationships
    rel = []
    sc = link_list(RDFS.subClassOf)
    if sc:
        rel.append(("Sub-class of", sc))
    sp = link_list(RDFS.subPropertyOf)
    if sp:
        rel.append(("Sub-property of", sp))
    dom = link_list(RDFS.domain)
    if dom:
        rel.append(("Domain", dom))
    rng = link_list(RDFS.range)
    if rng:
        rel.append(("Range", rng))
    inv = link_list(OWL.inverseOf)
    inv2 = link_list(OWL.inverseOf, reverse=True)
    inv_all = ", ".join(x for x in (inv, inv2) if x)
    if inv_all:
        rel.append(("Inverse of", inv_all))
    same = link_list(OWL.sameAs)
    if same:
        rel.append(("Same as", same))

    for k, v in rel:
        rows.append((k, v))

    # class-specific: in domain/range of + shape
    extra_html = ""
    if kind_tag == "c":
        idf = in_domain_of(g, s, props_all)
        irf = in_range_of(g, s, props_all)
        if idf:
            rows.append(("In domain of", ", ".join(link(g, p, known) for p in idf)))
        if irf:
            rows.append(("In range of", ", ".join(link(g, p, known) for p in irf)))
        shape_rows, closed = shape_for_class(g, s)
        if shape_rows:
            hdr = "Validation constraints (SHACL)"
            if closed:
                hdr += " — closed shape"
            trs = []
            for r in shape_rows:
                typ = ""
                if r["datatype"] is not None:
                    typ = curie(g, r["datatype"])
                elif r["class"] is not None:
                    typ = curie(g, r["class"])
                elif r["nodeKind"] is not None:
                    typ = local(r["nodeKind"])
                note = ""
                if r["pattern"] is not None:
                    note = f'<code>{escape(str(r["pattern"]))}</code>'
                elif r["desc"] is not None:
                    note = escape(str(r["desc"]))
                trs.append(
                    "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                        escape(curie(g, r["path"])),
                        escape(typ),
                        escape(card(r["minCount"], r["maxCount"])),
                        note,
                    )
                )
            extra_html = (
                f'<table class="shape-table"><caption style="text-align:left;'
                f'font-weight:600;margin:10px 0 4px">{escape(hdr)}</caption>'
                "<tr><th>Property path</th><th>Type</th><th>Cardinality</th>"
                "<th>Pattern / note</th></tr>" + "".join(trs) + "</table>"
            )

    table = "".join(f"<tr><th>{escape(k)}</th><td>{v}</td></tr>" for k, v in rows)
    dep = '<span class="dep">deprecated</span>' if is_deprecated(g, s) else ""
    head = (
        f'<h3 id="{anchor(s)}"><sup class="t {kind_tag}">{kind_tag}</sup>'
        f'<span>{escape(get_label(g, s))}</span> {badge(s)} {dep}'
        f'<span class="back"><a href="#toc">↑ toc</a></span></h3>'
    )
    return f'<div class="entity">{head}<table>{table}</table>{extra_html}</div>'


def graph_payload(g, classes, obj_props, data_props, individuals):
    """Build a schema-level graph: entity/enumeration classes as nodes, object
    properties as directed (domain -> range) edges, datatype properties listed
    per node. Edges are sourced from SHACL node shapes (richest signal) and from
    rdfs:domain/range (covers the v3 additions that have no shapes yet)."""
    enum_classes = set(g.subjects(RDFS.subClassOf, SCHEMA.Enumeration))
    node_uris = [c for c in classes if c != SCHEMA.Enumeration]
    curie_to_uri = {curie(g, c): c for c in node_uris}

    nodes = {}
    for c in node_uris:
        cc = curie(g, c)
        cat = "enum" if c in enum_classes else "entity"
        label = get_label(g, c)
        if cat == "enum":
            label = re.sub(r"\s*Enumeration$", "", label)
        nodes[cc] = {
            "data": {
                "id": cc,
                "label": label,
                "curie": cc,
                "cat": cat,
                "ns": ns_prefix(c),
                "desc": get_desc(g, c),
                "props": [],
                "count": len(individuals.get(c, [])) if cat == "enum" else 0,
            }
        }

    edges = {}

    def add_edge(srcc, tgtc, label):
        if srcc in nodes and tgtc in nodes and srcc != tgtc:
            k = (srcc, tgtc, label)
            if k not in edges:
                edges[k] = {
                    "data": {"id": f"e{len(edges)}", "source": srcc, "target": tgtc, "label": label}
                }
        elif srcc in nodes and tgtc in nodes and srcc == tgtc:
            k = (srcc, tgtc, label)
            edges.setdefault(
                k,
                {"data": {"id": f"e{len(edges)}", "source": srcc, "target": tgtc, "label": label}},
            )

    def add_prop(nodec, name, typ, cardinality):
        if nodec in nodes:
            nodes[nodec]["data"]["props"].append({"name": name, "type": typ, "card": cardinality})

    # --- edges + datatype attrs from SHACL node shapes ---
    for shape in g.subjects(RDF.type, SH.NodeShape):
        cls = g.value(shape, SH.targetClass)
        if cls is None:
            continue
        cc = curie(g, cls)
        for pshape in g.objects(shape, SH.property):
            path = g.value(pshape, SH.path)
            if path is None:
                continue
            pc = curie(g, path)
            targets = []
            direct = g.value(pshape, SH["class"])
            if direct is not None:
                targets.append(direct)
            ornode = g.value(pshape, SH["or"])
            if ornode is not None:
                for item in g.items(ornode):
                    x = g.value(item, SH["class"])
                    if x is not None:
                        targets.append(x)
            for d in targets:
                add_edge(cc, curie(g, d), pc)
            dt = g.value(pshape, SH.datatype)
            if dt is not None and not targets:
                add_prop(cc, pc, curie(g, dt), card(g.value(pshape, SH.minCount), g.value(pshape, SH.maxCount)))

    # --- object-property edges from rdfs:domain/range (v3 additions) ---
    for p in obj_props:
        pc = curie(g, p)
        dom = g.value(p, RDFS.domain)
        rng = g.value(p, RDFS.range)
        domc = curie(g, dom) if dom is not None else SUPPLEMENT_DOMAIN.get(pc)
        rngc = curie(g, rng) if rng is not None else None
        if domc and rngc:
            add_edge(domc, rngc, pc)

    # --- datatype-property attrs from rdfs:domain (v3 additions) ---
    for p in data_props:
        pc = curie(g, p)
        dom = g.value(p, RDFS.domain)
        rng = g.value(p, RDFS.range)
        domc = curie(g, dom) if dom is not None else SUPPLEMENT_DOMAIN.get(pc)
        if domc:
            add_prop(domc, pc, curie(g, rng) if rng is not None else "", "")

    # dedupe props per node by name (SHACL + rdfs may overlap)
    for nd in nodes.values():
        seen, uniq = set(), []
        for pr in nd["data"]["props"]:
            if pr["name"] in seen:
                continue
            seen.add(pr["name"])
            uniq.append(pr)
        uniq.sort(key=lambda x: x["name"])
        nd["data"]["props"] = uniq

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


COMPONENT_CSS = """
.graph-intro{font-size:.92rem;color:#444;margin:6px 0 10px}
.graph-host{margin:8px 0 26px}
.graph-panel{border:1px solid #d7dee1;border-radius:8px;overflow:hidden;background:#fff}
.graph-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;padding:8px 12px;background:#f3f6f7;border-bottom:1px solid #e1e8ea}
.graph-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:.78rem;align-items:center}
.graph-legend .chip{display:inline-flex;align-items:center;gap:5px}
.graph-legend .dot{width:13px;height:13px;border-radius:3px;display:inline-block}
.graph-controls{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.graph-controls button{font:inherit;font-size:.8rem;border:1px solid #c4ced2;background:#fff;color:#28424b;border-radius:5px;padding:4px 10px;cursor:pointer}
.graph-controls button:hover{background:#e9f1f3}
.graph-controls button.primary{background:#1f6f8b;color:#fff;border-color:#1f6f8b}
.graph-controls button.primary:hover{background:#185667}
.graph-controls label{font-size:.78rem;display:inline-flex;gap:4px;align-items:center;color:#33484f}
.graph-controls input.search,.graph-controls select.gsel{font:inherit;font-size:.8rem;padding:4px 8px;border:1px solid #c4ced2;border-radius:5px;background:#fff;color:#28424b}
.graph-controls input.search{width:140px}
.graph-stage{position:relative;display:flex;height:500px}
.graph-cy{flex:1 1 auto;height:100%;min-width:0;background:
  linear-gradient(#fbfcfd 0 0) padding-box,
  radial-gradient(#e7edef 1px,transparent 1px);background-size:auto,18px 18px;background-color:#fbfcfd}
.graph-info{flex:0 0 300px;border-left:1px solid #e1e8ea;padding:12px 14px;overflow:auto;font-size:.84rem;display:none;background:#fff}
.graph-panel.is-fullscreen .graph-info{display:block;flex:0 0 25vw}
.graph-info h4{margin:0 0 2px;font-size:1rem;word-break:break-word}
.graph-info .muted{color:#8a8a8a}
.graph-info .kindtag{display:inline-block;margin:5px 0 8px;padding:2px 9px;border-radius:10px;font-size:.66rem;font-weight:700;text-transform:uppercase;letter-spacing:.03em;color:#fff}
.graph-info table{font-size:.8rem;margin:6px 0 14px;width:100%;border-collapse:collapse}
.graph-info th,.graph-info td{border:1px solid #e3e8ea;padding:4px 7px;text-align:left;vertical-align:top}
.graph-info th{width:38%;background:#f3f6f7;font-weight:600}
.graph-info .reltag{display:block;margin:2px 0;font-family:monospace;font-size:.78rem;color:#3a4a50}
.graph-missing{padding:24px;color:#777;font-size:.9rem}
.graph-hint{position:absolute;left:10px;bottom:8px;font-size:.72rem;color:#7a8a90;background:rgba(255,255,255,.82);padding:2px 8px;border-radius:10px;pointer-events:none}
.graph-panel.is-fullscreen{position:fixed;inset:0;z-index:9999;border-radius:0;display:flex;flex-direction:column;margin:0}
.graph-panel.is-fullscreen .graph-stage{flex:1 1 auto;height:auto}
body.graph-locked{overflow:hidden}
"""

# Reusable Cytoscape viewer shared by every generated page. Renders its own
# toolbar (dataset switcher, layout selector, search, toggles, zoom, full
# screen) into a host element from a list of datasets supplied at mount time.
COMPONENT_JS = r"""
(function () {
  function elt(tag, cls, html){ var e=document.createElement(tag); if(cls) e.className=cls; if(html!=null) e.innerHTML=html; return e; }
  function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }

  var STYLE = [
    { selector:'node', style:{
        'shape':'round-rectangle','label':'data(label)','font-size':12,'font-weight':600,
        'color':'#fff','text-valign':'center','text-halign':'center','text-wrap':'wrap',
        'text-max-width':'160px','padding':'9px','width':'label','height':'label',
        'background-color':'data(color)','border-width':0 } },
    { selector:'node[variant="root"]', style:{ 'font-size':14,'border-width':2,'border-color':'#11333d','padding':'12px' } },
    { selector:'node[variant="enum"]', style:{ 'color':'#5a4500','border-width':1.5,'border-style':'dashed','border-color':'#b8860b' } },
    { selector:'node[variant="ref"]', style:{ 'border-width':1.5,'border-style':'dashed','border-color':'#7a3fb0' } },
    { selector:'node[variant="scalar"]', style:{ 'font-size':10,'font-weight':500 } },
    { selector:'edge', style:{
        'curve-style':'taxi','taxi-direction':'rightward','taxi-turn':'40%','taxi-turn-min-distance':'8px',
        'width':1.6,'line-color':'#a7bac3','target-arrow-color':'#a7bac3','target-arrow-shape':'triangle',
        'arrow-scale':0.95,'label':'data(label)','font-size':9,'color':'#566970',
        'text-background-color':'#fbfcfd','text-background-opacity':0.92,'text-background-padding':2,
        'text-rotation':'none','min-zoomed-font-size':7 } },
    { selector:'edge[dashed="1"]', style:{ 'line-style':'dashed' } },
    { selector:'node.faded', style:{ 'opacity':0.16 } },
    { selector:'edge.faded', style:{ 'opacity':0.06 } },
    { selector:'node.hl', style:{ 'border-width':3,'border-color':'#ff8c00','border-style':'solid' } },
    { selector:'edge.hl', style:{ 'line-color':'#ff8c00','target-arrow-color':'#ff8c00','width':2.6,'color':'#9a4b00','z-index':99 } },
    { selector:'node:selected', style:{ 'border-width':3,'border-color':'#ff8c00','border-style':'solid' } }
  ];

  function mount(host, config){
    if(!host) return;
    if(typeof cytoscape === 'undefined'){
      host.innerHTML = "<div class='graph-missing'>The interactive graph needs the Cytoscape library (loaded from a CDN); it appears unavailable offline. The rest of the page works without it.</div>";
      return;
    }
    var hasDagre=false;
    try { if(window.cytoscapeDagre){ cytoscape.use(window.cytoscapeDagre); hasDagre=true; } } catch(e){}

    var datasets = config.datasets || [];
    if(!datasets.length){ host.innerHTML="<div class='graph-missing'>No graph data.</div>"; return; }

    var panel = elt('div','graph-panel');
    var toolbar = elt('div','graph-toolbar');
    var legend = elt('div','graph-legend');
    var controls = elt('div','graph-controls');

    // dataset switcher
    var swSel=null;
    if(datasets.length>1){
      swSel = elt('select','gsel');
      datasets.forEach(function(d,i){ var o=elt('option',null,esc(d.label)); o.value=i; swSel.appendChild(o); });
      var l1=elt('label',null,'View: '); l1.appendChild(swSel); controls.appendChild(l1);
    }
    // layout selector
    var laySel = elt('select','gsel');
    [['hier','Hierarchical'],['cose','Force'],['concentric','Concentric'],['grid','Grid'],['circle','Circle']].forEach(function(o){
      var op=elt('option',null,o[1]); op.value=o[0]; laySel.appendChild(op);
    });
    var l2=elt('label',null,'Layout: '); l2.appendChild(laySel); controls.appendChild(l2);
    // search
    var search=elt('input','search'); search.type='search'; search.placeholder='search node…'; controls.appendChild(search);
    // edge-label toggle
    var tglWrap=elt('label'); var tgl=elt('input'); tgl.type='checkbox'; tgl.checked=true;
    tglWrap.appendChild(tgl); tglWrap.appendChild(document.createTextNode(' Edge labels')); controls.appendChild(tglWrap);
    // buttons
    function mkbtn(txt,cls){ var b=elt('button',cls,txt); controls.appendChild(b); return b; }
    var bMinus=mkbtn('−'), bPlus=mkbtn('+'), bFit=mkbtn('Fit'), bRelay=mkbtn('Re-layout'), bFull=mkbtn('⤢ Full screen','primary');

    toolbar.appendChild(legend); toolbar.appendChild(controls);
    var stage=elt('div','graph-stage');
    var cyEl=elt('div','graph-cy');
    var hint=elt('div','graph-hint','drag to pan · scroll to zoom · click a node');
    var info=elt('aside','graph-info','<p class="muted">Select a node to see its details.</p>');
    stage.appendChild(cyEl); stage.appendChild(hint); stage.appendChild(info);
    panel.appendChild(toolbar); panel.appendChild(stage);
    host.appendChild(panel);

    var cy = cytoscape({ container:cyEl, style:STYLE, wheelSensitivity:0.22, minZoom:0.1, maxZoom:4 });

    function layoutObj(kind){
      if(kind==='hier') return hasDagre
        ? { name:'dagre', rankDir:'LR', nodeSep:42, edgeSep:14, rankSep:96, ranker:'tight-tree', animate:false, fit:true, padding:30 }
        : { name:'breadthfirst', directed:true, spacingFactor:1.35, fit:true, padding:30 };
      if(kind==='cose') return { name:'cose', animate:false, fit:true, padding:30, nodeRepulsion:9500, idealEdgeLength:115, nodeOverlap:18, gravity:0.22, numIter:1400, coolingFactor:0.96 };
      if(kind==='concentric') return { name:'concentric', fit:true, padding:30, minNodeSpacing:34, concentric:function(n){return n.degree();}, levelWidth:function(){return 2;} };
      if(kind==='grid') return { name:'grid', fit:true, padding:30, avoidOverlap:true };
      if(kind==='circle') return { name:'circle', fit:true, padding:30 };
      return { name:'grid' };
    }
    function applyEdgeShape(kind){
      if(kind==='hier') cy.edges().style({ 'curve-style':'taxi','taxi-direction':'rightward','taxi-turn':'40%' });
      else cy.edges().style({ 'curve-style':'bezier' });
    }
    function relayout(){ var k=laySel.value; cy.layout(layoutObj(k)).run(); applyEdgeShape(k); cy.edges().style('text-opacity', tgl.checked?1:0); }

    function clearInfo(){ info.innerHTML='<p class="muted">Select a node to see its details.</p>'; }
    function showInfo(node){
      var d=node.data();
      var h='<h4>'+esc(d.label)+'</h4>';
      if(d.curie) h+='<div><code>'+esc(d.curie)+'</code></div>';
      if(d.kind) h+='<div class="kindtag" style="background:'+(d.color||'#888')+'">'+esc(d.kind)+'</div>';
      if(d.desc) h+='<p>'+esc(d.desc)+'</p>';
      if(d.count) h+='<p class="muted">'+d.count+' named individual'+(d.count===1?'':'s')+'</p>';
      if(d.info && d.info.length){ h+='<table>'; d.info.forEach(function(r){ h+='<tr><th>'+esc(r.k)+'</th><td>'+esc(r.v)+'</td></tr>'; }); h+='</table>'; }
      if(d.props && d.props.length){
        h+='<strong>Datatype properties</strong><table><tr><th>name</th><th>type</th></tr>';
        d.props.forEach(function(p){ h+='<tr><td><code>'+esc(p.name)+'</code></td><td>'+esc(p.type||'')+(p.card?(' <span class="muted">('+esc(p.card)+')</span>'):'')+'</td></tr>'; });
        h+='</table>';
      }
      var outE=node.outgoers('edge'), inE=node.incomers('edge');
      if(outE.length){ h+='<strong>Relations out</strong>'; outE.forEach(function(e){ h+='<span class="reltag">— '+esc(e.data('label')||'')+' → '+esc(e.target().data('label'))+'</span>'; }); }
      if(inE.length){ h+='<strong>Relations in</strong>'; inE.forEach(function(e){ h+='<span class="reltag">← '+esc(e.source().data('label'))+(e.data('label')?(' · '+esc(e.data('label'))):'')+'</span>'; }); }
      info.innerHTML=h;
    }
    function highlight(node){
      cy.elements().addClass('faded').removeClass('hl');
      node.closedNeighborhood().removeClass('faded').addClass('hl');
      node.connectedEdges().removeClass('faded').addClass('hl');
    }
    function unhighlight(){ cy.elements().removeClass('faded').removeClass('hl'); }

    cy.on('tap','node',function(evt){ highlight(evt.target); showInfo(evt.target); });
    cy.on('tap',function(evt){ if(evt.target===cy){ unhighlight(); clearInfo(); cy.$(':selected').unselect(); } });

    function loadDataset(ds){
      cy.elements().remove();
      cy.add(ds.elements.nodes.concat(ds.elements.edges));
      legend.innerHTML='';
      (ds.legend||[]).forEach(function(L){
        var c=elt('span','chip');
        c.innerHTML="<span class='dot' style='background:"+L.color+(L.dashed?";border:1.5px dashed #b8860b":"")+"'></span>"+esc(L.label);
        legend.appendChild(c);
      });
      laySel.value = ds.layout || 'hier';
      clearInfo();
      relayout();
      setTimeout(function(){ cy.resize(); cy.fit(undefined,28); }, 70);
    }

    if(swSel) swSel.addEventListener('change', function(){ loadDataset(datasets[+swSel.value]); });
    laySel.addEventListener('change', relayout);
    tgl.addEventListener('change', function(){ cy.edges().style('text-opacity', tgl.checked?1:0); });
    bFit.addEventListener('click', function(){ cy.animate({ fit:{ padding:30 }, duration:250 }); });
    bRelay.addEventListener('click', relayout);
    bPlus.addEventListener('click', function(){ cy.zoom({ level:cy.zoom()*1.3, renderedPosition:{ x:cy.width()/2, y:cy.height()/2 } }); });
    bMinus.addEventListener('click', function(){ cy.zoom({ level:cy.zoom()/1.3, renderedPosition:{ x:cy.width()/2, y:cy.height()/2 } }); });
    search.addEventListener('input', function(){
      var q=search.value.trim().toLowerCase();
      if(!q){ unhighlight(); return; }
      var m=cy.nodes().filter(function(n){ var d=n.data(); return ((d.label||'')+' '+(d.curie||'')).toLowerCase().indexOf(q)!==-1; });
      if(m.length){ highlight(m[0]); showInfo(m[0]); cy.animate({ center:{ eles:m[0] }, zoom:1.1, duration:250 }); }
    });

    function toggleFull(){
      var on=panel.classList.toggle('is-fullscreen');
      document.body.classList.toggle('graph-locked', on);
      bFull.textContent = on ? '✕ Exit full screen' : '⤢ Full screen';
      setTimeout(function(){ cy.resize(); cy.fit(undefined,34); }, 70);
    }
    bFull.addEventListener('click', toggleFull);
    document.addEventListener('keydown', function(e){ if(e.key==='Escape' && panel.classList.contains('is-fullscreen')) toggleFull(); });

    loadDataset(datasets[0]);
  }

  window.PulseGraph = { mount: mount };
})();
"""

CDN_TAGS = (
    "<script src='https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js'></script>"
    "<script src='https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js'></script>"
    "<script src='https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js'></script>"
)


def write_component_files(out_dir):
    (out_dir / "graph.css").write_text(COMPONENT_CSS)
    (out_dir / "graph.js").write_text(COMPONENT_JS)


def mount_script(host_id, datasets) -> str:
    import json

    data = json.dumps(datasets, ensure_ascii=False).replace("</", "<\\/")
    return (
        CDN_TAGS
        + "<script src='graph.js'></script>"
        + "<script>PulseGraph.mount(document.getElementById('"
        + host_id
        + "'), {datasets: "
        + data
        + "});</script>"
    )


# --------------------------------------------------------------------------- #
#  Dataset builders                                                            #
# --------------------------------------------------------------------------- #
NSCOLOR = {"pulse": "#1f6f8b", "schema": "#3b5bdb", "org": "#2e8b57", "prov": "#d9822b", "time": "#7a7a7a", "other": "#6b7a80"}
ENUM_FILL = "#f5d98a"
NS_KIND = {"pulse": "Open Pulse class", "schema": "schema.org class", "org": "ORG class", "prov": "PROV class"}

SCHEMA_DIR = ROOT / "git_metadata_extractor/schema/json"
ENTITIES = ["person", "organization", "repository", "article", "contribution", "membership"]
ENTITY_COLOR = {"person": "#3b5bdb", "organization": "#2e8b57", "repository": "#1f6f8b",
                "article": "#b8536b", "contribution": "#d9822b", "membership": "#7a3fb0"}
VARIANT_COLOR = {"root": "#13404f", "object": "#4a63c8", "array": "#2e8b57",
                 "ref": "#7a3fb0", "enum": ENUM_FILL, "scalar": "#7c8a91"}
SCHEMA_LEGEND = [
    {"label": "Root", "color": VARIANT_COLOR["root"]},
    {"label": "Object", "color": VARIANT_COLOR["object"]},
    {"label": "Array", "color": VARIANT_COLOR["array"]},
    {"label": "Reference", "color": VARIANT_COLOR["ref"], "dashed": True},
    {"label": "Enum / const", "color": VARIANT_COLOR["enum"], "dashed": True},
    {"label": "Scalar", "color": VARIANT_COLOR["scalar"]},
]
NAME_REF = {
    "org:hasMembership": "MembershipShape", "pulse:hasContribution": "ContributionShape",
    "pulse:owns": "RepositoryShape", "pulse:ownedBy": "PersonShape",
    "pulse:contributionTo": "RepositoryShape", "org:organization": "OrganizationShape",
    "schema:author": "PersonShape", "schema:sourceOrganization": "OrganizationShape",
    "pulse:isForkOf": "RepositoryShape", "org:hasUnit": "OrganizationShape",
    "org:unitOf": "OrganizationShape",
}


def ontology_dataset(g, classes, obj_props, data_props, individuals):
    payload = graph_payload(g, classes, obj_props, data_props, individuals)
    for n in payload["nodes"]:
        d = n["data"]
        if d["cat"] == "enum":
            d["variant"], d["color"], d["kind"] = "enum", ENUM_FILL, "Enumeration"
        else:
            d["variant"] = "entity"
            d["color"] = NSCOLOR.get(d["ns"], NSCOLOR["other"])
            d["kind"] = NS_KIND.get(d["ns"], "Class")
    legend = [
        {"label": "Open Pulse", "color": NSCOLOR["pulse"]},
        {"label": "schema.org", "color": NSCOLOR["schema"]},
        {"label": "ORG", "color": NSCOLOR["org"]},
        {"label": "PROV", "color": NSCOLOR["prov"]},
        {"label": "Enumeration", "color": ENUM_FILL, "dashed": True},
    ]
    return {"key": "ontology", "label": "Ontology v3.0.0", "legend": legend,
            "layout": "hier", "elements": {"nodes": payload["nodes"], "edges": payload["edges"]}}


def _types(sub):
    t = sub.get("type") if isinstance(sub, dict) else None
    if isinstance(t, list):
        return list(t)
    return [t] if t else []


def _ref_target(sub, name=None):
    texts = []
    if isinstance(sub, dict):
        if sub.get("description"):
            texts.append(sub["description"])
        items = sub.get("items")
        if isinstance(items, dict) and items.get("description"):
            texts.append(items["description"])
    for t in texts:
        m = re.search(r"([A-Z][A-Za-z]+Shape)", t)
        if m:
            return m.group(1)
    return NAME_REF.get(name) if name else None


def schema_structure_dataset(path, label, accent):
    import json

    sch = json.loads(Path(path).read_text())
    nodes, edges, seen = [], [], set()

    def add_node(nid, lbl, variant, desc="", info=None, color=None):
        if nid in seen:
            return
        seen.add(nid)
        nodes.append({"data": {"id": nid, "label": lbl, "variant": variant,
                               "kind": variant.capitalize(), "desc": desc,
                               "color": color or VARIANT_COLOR.get(variant, "#888"),
                               "info": info or []}})

    def add_edge(a, b, lbl="", dashed=False):
        edges.append({"data": {"id": "e" + str(len(edges)), "source": a, "target": b,
                               "label": lbl, "dashed": "1" if dashed else "0"}})

    title = sch.get("title", label)
    root_info = [
        {"k": "type", "v": ", ".join(_types(sch)) or "object"},
        {"k": "required", "v": str(len(sch.get("required", [])))},
        {"k": "additionalProperties", "v": str(sch.get("additionalProperties", True))},
    ]
    add_node("root", title, "root", sch.get("description", ""), root_info, accent)
    req = set(sch.get("required", []))

    for name, sub in (sch.get("properties") or {}).items():
        nid = "p_" + name
        ts = _types(sub)
        ref = _ref_target(sub, name)
        info = [{"k": "type", "v": ", ".join(ts) or ("enum" if "enum" in sub else ("const" if "const" in sub else "—"))}]
        if name in req:
            info.append({"k": "required", "v": "yes"})
        if sub.get("pattern"):
            info.append({"k": "pattern", "v": sub["pattern"]})
        if sub.get("format"):
            info.append({"k": "format", "v": sub["format"]})
        if "enum" in sub:
            variant = "enum"
            info.append({"k": "enum", "v": ", ".join(map(str, sub["enum"]))})
        elif "const" in sub:
            variant = "enum"
            info.append({"k": "const", "v": str(sub["const"])})
        elif "object" in ts:
            variant = "object"
        elif "array" in ts:
            variant = "ref" if ref else "array"
            if ref:
                info.append({"k": "items →", "v": ref})
        else:
            variant = "ref" if ref else "scalar"
            if ref:
                info.append({"k": "→", "v": ref})
        add_node(nid, name, variant, (sub.get("description") or ""), info)
        add_edge("root", nid, ref or "", dashed=(name not in req))
        # one level of nesting for object properties (e.g. identifiers)
        if "object" in ts and isinstance(sub.get("properties"), dict):
            sreq = set(sub.get("required", []))
            for cn, cs in sub["properties"].items():
                cid = nid + "/" + cn
                cinfo = [{"k": "type", "v": ", ".join(_types(cs)) or "—"}]
                if cs.get("pattern"):
                    cinfo.append({"k": "pattern", "v": cs["pattern"]})
                cvar = "enum" if "enum" in cs else "scalar"
                add_node(cid, cn, cvar, (cs.get("description") or ""), cinfo)
                add_edge(nid, cid, "", dashed=(cn not in sreq))

    for key in ("anyOf", "oneOf", "allOf"):
        block = sch.get(key)
        if isinstance(block, list) and block:
            cid = "c_" + key
            opts = [o.get("description") or ", ".join(o.get("required", [])) for o in block]
            add_node(cid, key + " (" + str(len(block)) + ")", "enum", "Constraint group: " + key,
                     [{"k": str(i + 1), "v": o} for i, o in enumerate(opts)])
            add_edge("root", cid, key, dashed=True)

    return {"key": label, "label": label, "legend": SCHEMA_LEGEND, "layout": "hier",
            "elements": {"nodes": nodes, "edges": edges}}


def schema_overview_dataset(variant_dir):
    import json

    metas = {}
    for ent in ENTITIES:
        sch = json.loads((SCHEMA_DIR / variant_dir / (ent + ".schema.json")).read_text())
        metas[sch.get("title", ent)] = (ent, sch)
    titles = set(metas)

    nodes, edges = [], []
    for title, (ent, sch) in metas.items():
        props = sch.get("properties") or {}
        scalars = [k for k, v in props.items() if not _ref_target(v, k) and "object" not in _types(v)]
        nodes.append({"data": {
            "id": title, "label": title.replace("Shape", ""), "variant": "entity",
            "kind": "Entity schema", "color": ENTITY_COLOR.get(ent, "#666"),
            "desc": sch.get("description", ""),
            "info": [{"k": "properties", "v": str(len(props))},
                     {"k": "required", "v": str(len(sch.get("required", [])))},
                     {"k": "$id", "v": sch.get("$id", "")}],
            "props": [{"name": k, "type": ", ".join(_types(props[k]) or ["—"])} for k in scalars],
        }})
    seen = set()
    for title, (ent, sch) in metas.items():
        for name, sub in (sch.get("properties") or {}).items():
            ref = _ref_target(sub, name)
            if ref and ref in titles and ref != title and (title, ref, name) not in seen:
                seen.add((title, ref, name))
                edges.append({"data": {"id": "e" + str(len(edges)), "source": title, "target": ref, "label": name}})

    legend = [{"label": e.capitalize(), "color": ENTITY_COLOR[e]} for e in ENTITIES]
    return {"key": "overview", "label": "Entity overview (references)", "legend": legend,
            "layout": "hier", "elements": {"nodes": nodes, "edges": edges}}


def schema_datasets():
    ds = [schema_overview_dataset("strict")]
    for variant in ("strict", "agent"):
        for ent in ENTITIES:
            path = SCHEMA_DIR / variant / (ent + ".schema.json")
            ds.append(schema_structure_dataset(path, ent.capitalize() + " · " + variant, ENTITY_COLOR.get(ent, "#555")))
    return ds


def build_html(g: Graph) -> str:
    classes, obj_props, data_props, individuals, class_set, prop_set = classify(g)
    known = class_set | prop_set
    for inds in individuals.values():
        known |= set(inds)

    # the open-pulse ontology node specifically (graph also has the gme node)
    onto = PULSE[""] if (PULSE[""], RDF.type, OWL.Ontology) in g else None
    if onto is None:
        for o in g.subjects(RDF.type, OWL.Ontology):
            if "open-pulse" in str(o):
                onto = o
                break

    def ov(p):
        v = g.value(onto, p) if onto else None
        return escape(str(v)) if v else ""

    counts = {
        "Classes": len(classes),
        "Object properties": len(obj_props),
        "Datatype properties": len(data_props),
        "Named individuals": sum(len(v) for v in individuals.values()),
    }

    out = []
    out.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    out.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    out.append("<title>Open Pulse Ontology v3.0.0 (proposed)</title>")
    out.append(f"<style>{CSS}</style><link rel='stylesheet' href='graph.css'></head><body>")
    out.append(
        "<header class='site'><div class='inner'>"
        "<h1>Open Pulse Ontology</h1>"
        "<div class='sub'>Version <strong>v3.0.0</strong> · "
        "<em>proposed / draft</em> · models contributions to open-source "
        "scientific software and research articles at EPFL</div></div></header>"
    )
    out.append("<div id='wrap'>")

    # status banners
    out.append(
        "<div class='banner'><strong>Status:</strong> this is a <strong>proposed "
        "v3.0.0 draft</strong>, not a released version. The current production "
        "ontology is "
        "<a href='https://sdsc-ordes.github.io/open-pulse-ontology/versions/v2.1.2/'>"
        "v2.1.2</a>. This page merges all v2.1.2 terms carried forward, the proposed "
        "v3 additions (identity, provenance/observation, publications, organization "
        "relationships, cross-platform identifiers), and the internal provider "
        "vocabulary. Breaking changes vs. v2: <code>org:unitOf</code> becomes an array, "
        "<code>pulse:githubUsername</code> is deprecated, and <code>schema:citation</code> "
        "is clarified as a URI.</div>"
    )
    out.append(
        "<div class='banner gme'><strong>Internal fields:</strong> terms badged "
        "<span class='ns-badge ns-gme'>GME internal</span> "
        "(<code>gme-internal:</code>) are an auxiliary, <strong>non-normative</strong> "
        "vocabulary emitted only when an extract is requested with "
        "<code>?include_internal_fields=true</code>. They are valid RDF but are "
        "intentionally <strong>not</strong> conformant to the closed Open Pulse SHACL "
        "shapes.</div>"
    )

    # metadata
    out.append("<h2 id='metadata'>Metadata</h2><table class='meta-table'>")
    md = [
        ("IRI", f"<code>{escape(str(onto)) if onto else ''}</code>"),
        ("Title", ov(DCTERMS.title) or "Open Pulse Ontology"),
        ("Version", "v3.0.0 (proposed)"),
        ("Prior version", "<a href='https://sdsc-ordes.github.io/open-pulse-ontology/versions/v2.1.2/'>v2.1.2</a>"),
        ("Abstract", ov(DCTERMS.abstract)),
        ("License", "<a href='https://spdx.org/licenses/CC-BY-4.0.html'>CC-BY-4.0</a>"),
    ]
    for k, v in md:
        if v:
            out.append(f"<tr><th>{escape(k)}</th><td>{v}</td></tr>")
    out.append("</table>")
    out.append(
        "<p>"
        + "".join(f"<span class='pill'>{escape(k)}: {v}</span>" for k, v in counts.items())
        + "</p>"
    )

    # legend
    out.append(
        "<p class='legend'><strong>Legend:</strong> "
        "<span><sup class='t c'>c</sup> Class</span>"
        "<span><sup class='t op'>op</sup> Object property</span>"
        "<span><sup class='t dp'>dp</sup> Datatype property</span>"
        "<span><sup class='t ni'>ni</sup> Named individual</span></p>"
    )

    # interactive graph overview
    onto_ds = ontology_dataset(g, classes, obj_props, data_props, individuals)
    out.append(
        "<h2 id='graph'>Ontology graph</h2>"
        "<p class='graph-intro'>Visual overview of the entity classes and how they "
        "relate. Nodes are classes (coloured by namespace; dashed amber = enumeration "
        "types); directed edges are object properties routed domain&nbsp;→&nbsp;range. "
        "Use the <strong>Layout</strong> selector to re-arrange (hierarchical, force, "
        "concentric…), scroll to zoom, drag to pan, click a node to inspect its "
        "datatype properties and relations, then open <strong>full screen</strong> to "
        "explore. The same viewer is used for the "
        "<a href='json-schemas.html'>JSON Schemas</a>.</p>"
        "<div id='ontoHost' class='graph-host'></div>"
    )

    # TOC
    out.append("<h2 id='toc'>Table of contents</h2>")

    def toc_section(title, items, tag):
        h = [f"<h3>{escape(title)} <span class='mono'>({len(items)})</span></h3><div class='toc'>"]
        for s in items:
            h.append(
                f"<a href='#{anchor(s)}'><sup class='t {tag}'>{tag}</sup> "
                f"{escape(curie(g, s))}</a>"
            )
        h.append("</div>")
        return "".join(h)

    out.append(toc_section("Classes", classes, "c"))
    out.append(toc_section("Object properties", obj_props, "op"))
    out.append(toc_section("Datatype properties", data_props, "dp"))

    # sections
    out.append("<h2 id='classes'>Classes</h2>")
    for s in classes:
        out.append(term_block(g, s, "c", known, prop_set))

    out.append("<h2 id='objectproperties'>Object properties</h2>")
    for s in obj_props:
        out.append(term_block(g, s, "op", known, prop_set))

    out.append("<h2 id='datatypeproperties'>Datatype properties</h2>")
    for s in data_props:
        out.append(term_block(g, s, "dp", known, prop_set))

    out.append("<h2 id='namedindividuals'>Named individuals</h2>")
    for ec in sorted(individuals, key=lambda x: curie(g, x).lower()):
        out.append(f"<h3>Instances of {link(g, ec, known)}</h3>")
        out.append("<table><tr><th>IRI</th><th>Label</th><th>Sub-class of</th></tr>")
        for ind in individuals[ec]:
            sub = ", ".join(link(g, x, known) for x in g.objects(ind, RDFS.subClassOf))
            out.append(
                f"<tr id='{anchor(ind)}'><td><code>{escape(curie(g, ind))}</code></td>"
                f"<td>{escape(get_label(g, ind))}</td><td>{sub}</td></tr>"
            )
        out.append("</table>")

    # namespaces
    out.append("<h2 id='namespaces'>Namespaces</h2><table><tr><th>Prefix</th><th>IRI</th></tr>")
    for p, u in PREFIXES:
        out.append(f"<tr><td><code>{escape(p)}</code></td><td><code>{escape(u)}</code></td></tr>")
    out.append("</table>")

    out.append(
        "<footer>Generated from <code>open-pulse-ontology-v2.1.2.ttl</code>, "
        "<code>.internal/ontology-v3/07-ttl-draft.md</code> and "
        "<code>docs/gme-internal.ttl</code> by "
        "<code>scripts/build_ontology_v3_docs.py</code>. "
        "Source TTL: <a href='open-pulse-ontology-v3.0.0.ttl'>"
        "open-pulse-ontology-v3.0.0.ttl</a>.</footer>"
    )
    out.append("</div>")  # close #wrap
    out.append(mount_script("ontoHost", [onto_ds]))
    out.append("</body></html>")
    return "\n".join(out)


def build_schemas_html() -> str:
    """Standalone page: the same graph viewer over the project's JSON schemas —
    an entity-reference overview plus the structure of every strict/agent schema."""
    datasets = schema_datasets()
    out = []
    out.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    out.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    out.append("<title>GME JSON Schemas — graph viewer</title>")
    out.append(f"<style>{CSS}</style><link rel='stylesheet' href='graph.css'></head><body>")
    out.append(
        "<header class='site'><div class='inner'>"
        "<h1>GME JSON Schemas</h1>"
        "<div class='sub'>Interactive graph viewer for the <code>git_metadata_extractor/schema/json</code> "
        "validation schemas (strict &amp; agent variants)</div></div></header>"
    )
    out.append("<div id='wrap'>")
    out.append(
        "<div class='banner'><strong>What this is.</strong> The same viewer used in the "
        "<a href='index.html'>ontology reference</a>, applied to the project's JSON-Schema "
        "entity contracts. Pick a view from the <strong>View</strong> selector: the "
        "<em>Entity overview</em> shows the six entity schemas and the reference "
        "properties that link them; each <em>&lt;Entity&gt; · strict/agent</em> view shows "
        "that schema's structure — root → properties → nested objects, with type, "
        "enum/const, pattern and cross-schema references. Click any node for details.</div>"
    )
    out.append("<h2>Schema graph</h2>")
    out.append(
        "<p class='graph-intro'>Colours: dark&nbsp;=&nbsp;root, blue&nbsp;=&nbsp;object, "
        "green&nbsp;=&nbsp;array, purple&nbsp;dashed&nbsp;=&nbsp;reference to another "
        "schema, amber&nbsp;dashed&nbsp;=&nbsp;enum/const, grey&nbsp;=&nbsp;scalar. "
        "Dashed edges are optional properties; solid edges are required.</p>"
    )
    out.append("<div id='schemaHost' class='graph-host'></div>")
    out.append(
        "<h2>Source files</h2><p>Generated from "
        "<code>git_metadata_extractor/schema/json/{strict,agent}/&lt;entity&gt;.schema.json</code> "
        "(6 entities × 2 variants). Edit those, then re-run "
        "<code>python3 scripts/build_ontology_v3_docs.py</code>.</p>"
    )
    out.append(
        "<footer>Generated by <code>scripts/build_ontology_v3_docs.py</code>. "
        "<a href='index.html'>← Ontology v3.0.0 reference</a></footer>"
    )
    out.append("</div>")
    out.append(mount_script("schemaHost", datasets))
    out.append("</body></html>")
    return "\n".join(out)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ttl = normalize_literals(build_merged_ttl())
    OUT_TTL.write_text(ttl)
    g = Graph()
    g.parse(data=ttl, format="turtle")
    for p, u in PREFIXES:
        g.namespace_manager.bind(p, Namespace(u), replace=True)
    write_component_files(OUT_DIR)
    OUT_HTML.write_text(build_html(g))
    schemas_html = OUT_DIR / "json-schemas.html"
    schemas_html.write_text(build_schemas_html())
    print(f"merged TTL : {OUT_TTL.relative_to(ROOT)}  ({len(g)} triples)")
    print(f"HTML doc   : {OUT_HTML.relative_to(ROOT)}")
    print(f"schemas doc: {schemas_html.relative_to(ROOT)}")
    print(f"component  : graph.css + graph.js")


if __name__ == "__main__":
    main()
