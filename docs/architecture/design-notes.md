# Design Notes

This page documents the current runtime architecture in `v2.0.0`.

## Component architecture

```mermaid
flowchart TB
    subgraph Client
        C1[HTTP client / frontend]
    end

    subgraph API
        A1[FastAPI router<br/>src/api.py]
        A2[Request logging middleware<br/>AsyncRequestContext]
    end

    subgraph Analysis
        R[Repository analysis]
        U[User analysis]
        O[Organization analysis]
        AA[Atomic agents]
    end

    subgraph Data
        M[Data models<br/>src/data_models]
        Cache[SQLite cache<br/>src/cache]
    end

    subgraph External
        G[GitHub API]
        I[Infoscience API]
        ORCID[ORCID / Selenium]
        ROR[ROR API]
        LLM[Configured LLM provider]
        GIMIE[GIMIE]
    end

    C1 --> A2 --> A1
    A1 --> R
    A1 --> U
    A1 --> O

    R --> AA
    U --> AA
    O --> AA

    R --> M
    U --> M
    O --> M

    R --> Cache
    U --> Cache
    O --> Cache

    AA --> LLM
    R --> GIMIE
    R --> G
    U --> G
    O --> G
    AA --> I
    AA --> ROR
    R --> ORCID
```

## Repository request sequence

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant API as FastAPI /v1/repository/llm/json
    participant Repo as Repository.run_analysis
    participant Cache as CacheManager
    participant Pipe as Atomic pipeline + enrichments

    Client->>API: GET /v1/repository/llm/json/{url}
    API->>Repo: initialize + run_analysis(...)
    Repo->>Cache: check repository cache
    alt cache hit and not force_refresh
        Cache-->>Repo: cached object
        Repo-->>API: output + stats
    else cache miss or forced refresh
        Repo->>Pipe: run GIMIE + atomic stages
        Pipe-->>Repo: structured repository model
        Repo->>Pipe: optional enrichments + final EPFL assessment
        Repo->>Cache: persist final model
        Repo-->>API: output + stats
    end
    API-->>Client: APIOutput
```

## Notes

- Cache TTL defaults are configured in `src/cache/cache_config.py` (default 365 days unless overridden).
- Repository pipeline includes optional enrichments (`enrich_orgs`, `enrich_users`) and always runs final validation before caching.
- Organization analysis uses an atomic 6-stage flow; user analysis combines GitHub parsing + LLM + enrichment steps.
