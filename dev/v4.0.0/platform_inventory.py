"""Every platform we read, and every field we can get from it.

Derived, not typed. Four kinds of evidence, all local:

  local index      DuckDB column names and row counts under data/index/
  captured payload the response keys of the snapshots and live captures that
                   back the conversion test
  provider code    the fields package_registry_provider.py keeps per registry
  ontology         whether a field name matches a term, so coverage is visible

The point of generating it: a hand-written field list is wrong within a week,
and "which platform gives us X" is the question the ontology proposal keeps
having to answer.

Used by build_explorer.py (the Platforms view) and, run directly, writes
platform-fields.md.

Run:  python dev/v4.0.0/platform_inventory.py
"""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# One platform can own several indices — github/github_repos/github_users are
# three build targets of the same source, and listing them separately would
# suggest three platforms.
GROUPS: dict[str, list[str]] = {
    "GitHub": ["github", "github_repos", "github_users", "github_organizations"],
    "GitLab": ["gitlab_epfl_projects", "gitlab_epfl_groups", "gitlab_epfl_users",
               "gitlab_ethz_projects", "gitlab_ethz_groups", "gitlab_ethz_users",
               "gitlab_datascience_projects", "gitlab_datascience_groups",
               "gitlab_datascience_users"],
    "HuggingFace": ["huggingface", "huggingface_models", "huggingface_datasets",
                    "huggingface_spaces", "huggingface_organizations",
                    "huggingface_users", "huggingface_papers"],
    "ORCID": ["orcid-switzerland", "orcid-epfl"],
    "Zenodo": ["zenodo", "zenodo_records", "zenodo_communities", "communities"],
    "ROR": ["ror"],
    "Infoscience": ["infoscience"],
    "ETH Zurich Research Collection": ["ethz-research-collection"],
    "OpenAlex": ["openalex"],
    "SNSF": ["snsf"],
    "SWISSUbase": ["swissubase"],
    "RenkuLab": ["renkulab"],
    "EPFL Graph": ["epfl_graph"],
    "Docker Hub": ["dockerhub"],
    "Open Access Monitor": ["oamonitor"],
}

# How each platform is actually read. "wired" = a provider in production;
# "index" = an index exists and a RAG provider serves it; "test" = we read it in
# the conversion test but it is not wired as a provider yet. The distinction
# matters: the first two are things GME does today.
PROVIDERS: dict[str, tuple[str, str]] = {
    "GitHub": ("wired", "providers/github_provider.py, github_rag.py, github_accounts/"),
    "ORCID": ("wired", "providers/orcid_provider.py, orcid_rag.py, orcid_oauth.py"),
    "ROR": ("wired", "providers/ror_provider.py, ror_rag.py"),
    "Infoscience": ("wired", "providers/infoscience_provider.py, infoscience_rag.py"),
    "HuggingFace": ("index", "providers/huggingface_rag.py"),
    "Zenodo": ("index", "providers/zenodo_rag.py"),
    "OpenAlex": ("index", "providers/openalex_rag.py"),
    "SNSF": ("index", "providers/snsf_grants.py, snsf_rag.py"),
    "SWISSUbase": ("index", "providers/swissubase_rag.py"),
    "RenkuLab": ("index", "providers/renkulab_rag.py"),
    "EPFL Graph": ("index", "providers/epfl_graph_rag.py"),
    "ETH Zurich Research Collection": ("index", "providers/ethz_research_collection_rag.py"),
    "Open Access Monitor": ("index", "providers/oamonitor_rag.py"),
    "GitLab": ("index", "index only — no provider wired"),
    "Docker Hub": ("index", "index only; compose images resolved by the repository agent"),
    "Package registries": ("wired", "providers/package_registry_provider.py"),
    "deps.dev": ("test", "read in the conversion test; not wired as a provider"),
    "ecosyste.ms": ("test", "read in the conversion test; not wired as a provider"),
    "CORDIS": ("test", "read via OpenAIRE in the conversion test; not wired"),
}

# Columns that exist for the index's own plumbing, not because a source
# published them. Counting them as "unmapped fields" would overstate the gap.
HOUSEKEEPING = {
    "raw", "ingested_at", "fetched_at", "built_at_iso", "embedding_text",
    "search_blob", "vector_id", "embedded_at", "scope_mode", "scope_reason",
    "in_scope", "discovered_via", "discovered_at", "record", "hint",
    "embedding_model", "embedding_dim", "reranker_model", "raw_overview",
    "raw_dynamic_blocks", "seq", "position", "source", "mapping_direction",
}

PAYLOAD_PLATFORM = {
    "github": "GitHub", "ror": "ROR", "orcid": "ORCID", "infoscience": "Infoscience",
    "depsdev": "deps.dev", "dockerhub": "Docker Hub", "openalex": "OpenAlex",
    "hf": "HuggingFace", "ecosystems": "ecosyste.ms", "cordis": "CORDIS",
}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def build(terms: dict | None = None) -> dict:
    """Assemble the inventory. `terms` is build_explorer's term dict, if present."""
    # ---- a name index of ontology terms, for coverage marking
    term_by_norm: dict[str, str] = {}
    for curie, t in (terms or {}).items():
        local = curie.split(":", 1)[-1]
        term_by_norm.setdefault(norm(local), curie)
        for gme_field in t.get("mapsFrom", []):
            term_by_norm.setdefault(norm(gme_field), curie)

    def classify(field: str) -> dict:
        if field in HOUSEKEEPING or field.endswith("_id") and field.startswith("vector"):
            return {"name": field, "status": "housekeeping"}
        probe = re.sub(r"_(json|at|iso)$", "", field)
        cands = [probe, probe.replace("_", ""), field, re.sub(r"s$", "", probe)]
        for cand in cands:
            hit = term_by_norm.get(norm(cand))
            if hit:
                return {"name": field, "status": "matched", "term": hit}
        # "unmatched" means no term shares this name. It is a floor on coverage,
        # not a gap: ROR's `country_code` is mapped, just not by that name.
        return {"name": field, "status": "unmatched"}

    platforms: dict[str, dict] = {}

    def plat(name: str) -> dict:
        if name not in platforms:
            how, where = PROVIDERS.get(name, ("index", ""))
            platforms[name] = {"name": name, "how": how, "where": where,
                               "indices": [], "endpoints": [], "registries": []}
        return platforms[name]

    # ---- local indices
    try:
        import duckdb
    except ImportError:
        duckdb = None
    index_of = {idx: p for p, idxs in GROUPS.items() for idx in idxs}
    for d in sorted(glob.glob(str(ROOT / "data" / "index" / "*"))):
        idx = Path(d).name
        name = index_of.get(idx)
        if name is None:
            continue
        dbs = sorted(glob.glob(f"{d}/duckdb/*.duckdb")) + sorted(glob.glob(f"{d}/*.duckdb"))
        if not dbs or duckdb is None:
            continue
        try:
            con = duckdb.connect(dbs[0], read_only=True)
            tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        except Exception:
            continue
        for t in tables:
            if t == "chunks":          # RAG embedding chunks, not source fields
                continue
            try:
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
                rows = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            except Exception:
                continue
            plat(name)["indices"].append({
                "index": idx, "table": t, "rows": rows,
                "path": f"data/index/{idx}/{Path(dbs[0]).name}",
                "fields": [classify(c) for c in cols],
            })
        con.close()

    # ---- captured payloads: committed snapshots and live captures
    def paths_of(obj, prefix="", depth=0, out=None):
        out = out if out is not None else []
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{prefix}.{k}" if prefix else k
                out.append(p)
                if depth < 1:
                    paths_of(v, p, depth + 1, out)
        elif isinstance(obj, list) and obj:
            paths_of(obj[0], prefix + "[]", depth, out)
        return out

    SNAP = ROOT / "tests" / "v2" / "fixtures" / "providers" / "live_snapshots"
    mf = SNAP / "manifest.json"
    if mf.exists():
        for e in json.loads(mf.read_text(encoding="utf-8")).get("entries", []):
            rp, mp = SNAP / e.get("response_path", ""), SNAP / e.get("meta_path", "")
            if not rp.exists():
                continue
            url = ""
            if mp.exists():
                url = ((json.loads(mp.read_text(encoding="utf-8")).get("request") or {})
                       .get("url", ""))
            try:
                payload = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = PAYLOAD_PLATFORM.get(e.get("provider", ""), e.get("provider", ""))
            plat(name)["endpoints"].append({
                "kind": "committed snapshot", "case": e.get("case", ""), "url": url,
                "capturedAt": e.get("captured_at", ""),
                "fields": [classify(p.split(".")[-1].rstrip("[]")) | {"path": p}
                           for p in paths_of(payload)],
            })

    for p in sorted((HERE / "examples" / "sources").glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = PAYLOAD_PLATFORM.get(p.stem.split("_")[0], p.stem.split("_")[0])
        body = d.get("response", d) if isinstance(d, dict) else d
        req = d.get("request") if isinstance(d, dict) else None
        plat(name)["endpoints"].append({
            "kind": "live capture", "case": p.stem,
            "url": (req or {}).get("url", "") if isinstance(req, dict) else "",
            "capturedAt": (d.get("captured_at", "") if isinstance(d, dict) else ""),
            "fields": [classify(x.split(".")[-1].rstrip("[]")) | {"path": x}
                       for x in paths_of(body)],
        })

    # ---- package registries, from the provider's own thinners
    prp = ROOT / "git_metadata_extractor" / "providers" / "package_registry_provider.py"
    if prp.exists():
        src = prp.read_text(encoding="utf-8")
        for m in re.finditer(r"def _thin_(\w+)\(.*?:(.*?)(?=\n    @|\n    def |\ndef |\Z)",
                             src, re.S):
            reg, body = m.group(1), m.group(2)
            keys = list(dict.fromkeys(re.findall(r'"([a-z_][a-z0-9_]*)":', body)))
            if keys:
                plat("Package registries")["registries"].append({
                    "registry": reg, "fields": [classify(k) for k in keys]})

    # ---- fields GME computes rather than fetches
    # The biggest omission in a source-field inventory: badges, CI presence,
    # coverage, compose services and commit identities are parsed out of the
    # repository, so they appear in no API response and no index column. Every
    # gme:mapsFrom value names one of them.
    # read from the TTL rather than from `terms`, so the markdown report and the
    # explorer see the same list even though only the explorer passes terms in
    ttl = (HERE / "ontology.ttl").read_text(encoding="utf-8")
    derived = sorted({m for block in re.findall(r"gme:mapsFrom\s+([^.;]+)", ttl)
                      for m in re.findall(r'"([^"]+)"', block)})
    if derived:
        p = plat("GME-derived")
        p["how"] = "wired"
        p["where"] = ("computed by the extraction agents from parsed repository "
                      "content — no API returns these; each is a gme:mapsFrom value "
                      "in ontology.ttl")
        p["registries"].append({"registry": "parsed from the repository",
                                "fields": [classify(f) for f in derived]})

    # ---- totals
    for p in platforms.values():
        fields = ([f for i in p["indices"] for f in i["fields"]] +
                  [f for e in p["endpoints"] for f in e["fields"]] +
                  [f for r in p["registries"] for f in r["fields"]])
        real = [f for f in fields if f["status"] != "housekeeping"]
        p["totals"] = {
            "fields": len(real),
            "distinct": len({f["name"] for f in real}),
            "matched": len({f["name"] for f in real if f["status"] == "matched"}),
            "rows": sum(i["rows"] for i in p["indices"]),
            "tables": len(p["indices"]),
            "endpoints": len(p["endpoints"]),
            "registries": len(p["registries"]),
        }
    return {"platforms": [platforms[k] for k in sorted(platforms)],
            "housekeeping": sorted(HOUSEKEEPING)}


# --------------------------------------------------------------------- report
if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(HERE))
    inv = build()
    out = ["# Every platform, and the fields we can get from it", "",
           "Generated by [`platform_inventory.py`](platform_inventory.py) — do not",
           "hand-edit. Evidence is local: DuckDB column names and row counts under",
           "`data/index/`, the response keys of the captured payloads behind the",
           "conversion test, and the field lists in",
           "`providers/package_registry_provider.py`.", "",
           "`how` — **wired**: a provider runs in production · **index**: an index",
           "exists and a RAG provider serves it · **test**: read in the conversion",
           "test, not wired as a provider yet.", "",
           "Two caveats worth reading before using the counts. Captured-endpoint",
           "lists are the response keys verbatim, so GitHub's hypermedia keys",
           "(`events_url`, `hooks_url`, …) are in there as fields — they are the",
           "API's own plumbing, not data. And a table with **0 rows** has a schema",
           "but no ingest yet: the fields are obtainable, nothing has been fetched.", "",
           "| Platform | how | tables | rows | captured endpoints | registries | "
           "distinct fields |", "|---|---|---|---|---|---|---|"]
    for p in inv["platforms"]:
        t = p["totals"]
        out.append(f'| **{p["name"]}** | {p["how"]} | {t["tables"]} | {t["rows"]:,} | '
                   f'{t["endpoints"]} | {t["registries"] or ""} | {t["distinct"]} |')
    for p in inv["platforms"]:
        out += ["", f'## {p["name"]}', "", f'*{p["where"]}*', ""]
        for i in p["indices"]:
            names = ", ".join(f'`{f["name"]}`' for f in i["fields"]
                              if f["status"] != "housekeeping")
            out.append(f'- **{i["index"]}.{i["table"]}** — {i["rows"]:,} rows: {names}')
        for e in p["endpoints"]:
            names = ", ".join(sorted({f["name"] for f in e["fields"]
                                      if f["status"] != "housekeeping"}))
            out.append(f'- **{e["kind"]}** `{e["url"] or e["case"]}`: {names[:600]}')
        for r in p["registries"]:
            out.append(f'- **{r["registry"].replace("_", " ")}**: ' +
                       ", ".join(f'`{f["name"]}`' for f in r["fields"]))
    (HERE / "platform-fields.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    tot = sum(p["totals"]["distinct"] for p in inv["platforms"])
    print(f"{len(inv['platforms'])} platforms, {tot} distinct fields "
          f"-> dev/v4.0.0/platform-fields.md")
