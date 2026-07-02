# Git Metadata Extractor

A FastAPI service that turns a GitHub URL (repository / user / org) into
JSON-LD aligned with **Open Pulse Ontology v2.1.2**, plus nine sibling RAG
indices over EPFL/Swiss research catalogues that the v2 LLM agents can
query during extraction.

The repository ships **two cooperating subsystems**:

1. **Extraction service** (`src/v1/`, `src/v2/`) — the FastAPI app. V1 is
   frozen; all new work targets V2 under `src/v2/`.
2. **RAG indices** (`src/index/*`) — independent Qdrant + DuckDB indices
   over HuggingFace, OpenAlex, Infoscience, ETH Research Collection,
   ORCID, ROR, Zenodo, GitHub, SNSF, plus a federated layer that fans out
   across all of them.

## Documentation map

- [Getting Started](getting-started.md) — install, first run, common commands
- [V2 API Reference](v2-api-reference.md) — `/v2/extract`, `/v2/jobs`, `/v2/graph`
- [Migration: V1 → V2](migration-v1-to-v2.md) — endpoint mapping
- [API and CLI](api-and-cli.md) — quick reference
- [RAG Indices Overview](https://github.com/caviri/open-pulse-sources/blob/main/docs/rag-indices.md) — the nine indices + federated layer
- [Federated Search](https://github.com/caviri/open-pulse-sources/blob/main/docs/federated-search.md) — cross-index design
- [HuggingFace Index](https://github.com/caviri/open-pulse-sources/blob/main/docs/huggingface-index.md) — most-used index, deep-dive
- [V2 Agent RAG Tools](v2-rag-tools.md) — agent-side tools wired into the v2 pipeline
- [Roadmap](ROADMAP.md) — what's left to build
- [Design Notes](architecture/design-notes.md) — runtime architecture
- [Legacy Releases](releases/legacy-releases.md)

## V2 pipeline at a glance

`/v2/extract` runs the same 23-stage pipeline regardless of `agent_runtime`.
The runtime only controls which agent implementations execute (LLM vs.
deterministic rule-based). Other stages run unconditionally.

```mermaid
flowchart TB
    A[Client / batch script] --> B[FastAPI app<br/>src/api.py]
    B --> V2[/v2/extract /v2/jobs /v2/graph<br/>src/v2/api.py/]
    B --> V1[/v1/* legacy frozen/]

    V2 --> P[Pipeline orchestrator<br/>src/v2/pipeline/orchestrator.py]
    P --> CTX[context_summary]
    CTX --> AG[per-entity agents<br/>repo / person / org / article / membership / contribution]
    AG --> DEDUP[llm_dedup → reconcile → llm_critic]
    DEDUP --> VAL[strict_validation → assemble_output]
    VAL --> LV[link_veracity]
    LV --> OWN[ownership + org-hierarchy inference]
    OWN --> OUT[build_jsonld_output]

    AG -. RAG tools .-> RAG[src/v2/ingest/providers/*_rag.py]
    RAG --> Q[(Qdrant<br/>gme-qdrant:6333)]
    RAG --> RCP[EPFL RCP<br/>embed + rerank]
```

LLM agents call **per-index RAG tools** plus a **federated tool**
(`search_federated_rag`, `lookup_entity_federated`) that fans out across
nine indices in parallel. See
[V2 Agent RAG Tools](v2-rag-tools.md) for the full inventory.

## RAG indices at a glance

```mermaid
flowchart LR
    subgraph Sources
        HF[huggingface.co]
        OA[api.openalex.org]
        IS[infoscience.epfl.ch]
        ETHZ[research-collection.ethz.ch]
        OR[pub.orcid.org]
        RO[ror.org]
        ZE[zenodo.org]
        GH[api.github.com]
        SN[data.snf.ch]
    end

    subgraph Indices
        HFi[hf]
        OAi[openalex]
        ISi[infoscience]
        ETHi[ethz_research_collection]
        ORi[orcid]
        ROi[ror]
        ZEi[zenodo]
        GHi[github]
        SNi[snsf]
    end

    HF --> HFi
    OA --> OAi
    IS --> ISi
    ETHZ --> ETHi
    OR --> ORi
    RO --> ROi
    ZE --> ZEi
    GH --> GHi
    SN --> SNi

    Indices --> FED[Federated layer<br/>gme-search / gme-entity]
    FED --> AG[v2 LLM agents]
```

## Versioning

- `dev` and `latest` track the `main` branch documentation.
- Tagged releases publish immutable doc versions.
- `stable` points to the newest released docs.
