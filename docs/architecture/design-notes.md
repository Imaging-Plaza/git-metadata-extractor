# Design Notes

This page documents the current runtime architecture (`v2.0.0+`).

## Component architecture

```mermaid
flowchart TB
    subgraph Client
        C1[HTTP client / batch script]
    end

    subgraph API[FastAPI app — src/api.py]
        A1[v2_router<br/>src/v2/api.py]
        A2[Request logging<br/>AsyncRequestContext]
        A3[v1 frozen<br/>src/v1/]
    end

    subgraph Pipeline[V2 pipeline — src/v2/pipeline/]
        P0[orchestrator]
        P1[stages/* — 23 stages]
        P2[per-entity agents<br/>src/v2/agents/llm/* + rule_based/*]
    end

    subgraph Caches[Shared SQLite — V2_PROVIDER_CACHE_PATH]
        K1[Provider cache]
        K2[Agent verdict cache]
        K3[Pipeline response cache]
        K4[Async-job store]
    end

    subgraph Providers[src/v2/ingest/providers/]
        PG[GitHub REST + GIMIE]
        PR[ROR / ORCID / Infoscience]
        PRAG[*_rag — Qdrant-backed]
    end

    subgraph RAG[RAG indices — src/index/*]
        RH[HuggingFace]
        ROA[OpenAlex]
        RI[Infoscience]
        RE[ETHZ Research Collection]
        RO[ORCID]
        RR[ROR]
        RZ[Zenodo]
        RG[GitHub]
        RS[SNSF]
        RFED[Federated layer]
    end

    subgraph External
        GH[GitHub API]
        IS[Infoscience API]
        ORID[ORCID API]
        ROR[ROR API]
        SEL[Selenium grid]
        LLM[LLM provider<br/>RCP / OpenAI / OpenRouter]
        QD[(Qdrant<br/>gme-qdrant:6333)]
        RCP[EPFL RCP<br/>embed + rerank]
    end

    C1 --> A2 --> A1
    A1 --> P0
    A1 --> A3
    P0 --> P1
    P1 --> P2
    P2 --> Providers
    Providers --> Caches
    PG --> GH
    PR --> IS
    PR --> ORID
    PR --> ROR
    PRAG --> RFED
    PRAG --> RH
    PRAG --> ROA
    PRAG --> RI
    PRAG --> RE
    PRAG --> RO
    PRAG --> RR
    PRAG --> RZ
    RAG --> QD
    RAG --> RCP
    P1 --> SEL
    P2 --> LLM
```

## V2 extract sequence

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant API as /v2/extract
    participant Cache as ProviderCache (SQLite)
    participant Orch as Pipeline orchestrator
    participant Agents as Per-entity agents (LLM or rule-based)
    participant RAG as RAG providers + Qdrant
    participant Ext as External APIs (GitHub / ROR / ORCID / Infoscience / Selenium)

    Client->>API: GET or POST /v2/extract
    API->>Cache: pipeline-cache lookup (full response)
    alt pipeline cache hit
        Cache-->>API: V2ExtractResponse
        API-->>Client: response
    else miss
        API->>Orch: run pipeline
        Orch->>Ext: classify_url + gather_context (GIMIE + GitHub REST)
        Orch->>Cache: provider-cache lookup / store
        Orch->>Agents: context_summary → per-entity fan-out
        Agents->>Cache: agent-verdict cache lookup
        alt verdict miss
            Agents->>RAG: search_*_rag / lookup_*_rag (optional)
            RAG-->>Agents: thin hits
            Agents->>Cache: store verdict
        end
        Orch->>Orch: dedup → reconcile → critic → strict → assemble
        Orch->>Ext: link_veracity (Selenium fetch + LLM verdict)
        Orch->>Orch: ownership + org-hierarchy inference
        Orch->>Orch: build_jsonld_output
        Orch->>Cache: store pipeline response
        Orch-->>API: V2ExtractResponse
        API-->>Client: response
    end
```

For async submission (`POST /v2/extract`), the API persists a job record
into the same SQLite under the `v2-extract-job` namespace and runs the
pipeline as a background task. Clients poll `GET /v2/jobs/{job_id}`.

## Pipeline cache topology

Three caches share a single SQLite DB (path: `V2_PROVIDER_CACHE_PATH`):

1. **Provider cache** — gimie payloads, GitHub REST responses, ROR /
   ORCID / Infoscience hits. Deterministic, content-addressed.
2. **Agent verdict cache** — LLM agent results keyed on agent name +
   identity. Skips a repeat LLM call for a known-good payload.
3. **Pipeline cache** — full `/v2/extract` response for a given source URL.

The async-job store also lives in this SQLite (separate namespace, same
TTL). Set `V2_PROVIDER_CACHE_PATH` to a different file per run profile
(e.g. `.cache/v2-rule-based/providers.db` for rule-based runs) to keep
them isolated and independently invalidatable.

## RAG index architecture

Each index under `src/index/<name>/` is independent: its own DuckDB, its
own Qdrant collections, its own CLI, its own FastAPI app, its own refresh
cadence. The federated layer (`src/index/_federated/`) never shares
state — it just orchestrates fan-out across registered adapters in a
`ThreadPoolExecutor`.

```mermaid
flowchart LR
    Src[upstream API<br/>HF / OA / IS / …] --> ING[ingest CLI<br/>just <prefix>-ingest]
    ING --> DB[(DuckDB<br/>data/index/<name>/duckdb/)]
    DB --> EMB[embed CLI<br/>just <prefix>-embed]
    EMB --> RCPq[RCP /v1/embeddings]
    RCPq --> QD[(Qdrant<br/>gme-qdrant:6333)]
    DB --> Q[just <prefix>-query]
    QD --> S[just <prefix>-search]
    S --> RR[RCP /v1/rerank]
    RR --> Out[ranked hits]

    subgraph FED[Federated layer]
        ADAP[adapters/<name>.py]
    end
    DB -.-> ADAP
    QD -.-> ADAP
    ADAP --> GME[just gme-search / gme-entity]
    ADAP --> RAGTOOL[v2 LLM RAG tools]
```

Shared infrastructure (post-2026-05-01 pattern):

- **Storage**: DuckDB at `data/index/<name>/duckdb/<name>.duckdb` —
  canonical records + `chunks` ledger (one row per Qdrant point).
- **Vector store**: Qdrant — per-index collections, cosine distance,
  4096-dim vectors.
- **Embeddings**: `Qwen/Qwen3-Embedding-8B` on EPFL RCP, instruction-aware.
- **Reranker**: `Qwen/Qwen3-Reranker-8B` on EPFL RCP.
- **Chunking**: token-aware sliding window via `tiktoken` (`cl100k_base`).
- **Auth**: `RCP_TOKEN` (required); per-index source tokens (`HF_TOKEN`,
  `GME_GITHUB_TOKEN`, `INFOSCIENCE_TOKEN`, …) where the upstream API requires
  them.

The `ror` index is a partial outlier (no DuckDB layer; flat catalog of
orgs in Qdrant + a JSONL dump for lexical lookup).

See [RAG Indices Overview](https://github.com/caviri/open-pulse-sources/blob/main/docs/rag-indices.md) for the full inventory and
per-index quickstarts.

## Notes

- The frozen v1 pipeline (`src/v1/`) shares the FastAPI app but has its
  own cache (`src/v1/cache/`) and code paths. No new work targets v1.
- Internal pipeline metadata fields whose names start with `_` are
  stripped before strict validation, JSON-LD output, and any external
  artefact. Never expose `_`-prefixed fields in API responses.
- `identifiers.uuid` on every entity is server-generated
  (`src/v2/agents/models.py::generate_uuid()`); the LLM never controls it.
- See [V2 API Reference](../v2-api-reference.md) for the full 23-stage
  pipeline and gating rules.
