# Repository Analysis Agent Strategy

This document describes the current `Repository.run_analysis()` behavior in `src/analysis/repositories.py`.

## Pipeline flow

```mermaid
flowchart TD
    A[Input repository URL] --> B{Public repository}
    B -- No --> X[Stop with error]
    B -- Yes --> C{Cached and not force refresh}
    C -- Yes --> Z[Load cached result]
    C -- No --> D[Run GIMIE analysis]
    D --> E[Run atomic LLM pipeline]

    subgraph S1[Atomic LLM pipeline]
        E1[Prepare repository context]
        E2[Compile repository context]
        E3[Generate structured output]
        E4[Classify repository type and discipline]
        E5[Identify related organizations]
        E6[Build SoftwareSourceCode model]
    end

    E --> E1 --> E2 --> E3 --> E4 --> E5 --> E6
    E6 --> F[Run ORCID author enrichment]
    F --> G{Run user enrichment}
    G -- Yes --> H[Run user enrichment step]
    G -- No --> I[Skip user enrichment]
    H --> J{Run organization enrichment}
    I --> J
    J -- Yes --> K[Run organization enrichment step]
    J -- No --> L[Skip organization enrichment]
    K --> M[Run linked entities enrichment]
    L --> M
    M --> N{Run author linked entities}
    N -- Yes --> O[Run author linked entities step]
    N -- No --> P[Skip author linked entities]
    O --> Q[Run final EPFL assessment]
    P --> Q
    Q --> R[Run validation]
    R --> S[Save in cache]
    S --> T[Return output and usage stats]
```

## Core stages

1. Cache and repository accessibility checks.
2. GIMIE metadata retrieval.
3. Atomic LLM pipeline for core repository structure.
4. Optional enrichment branches (users, organizations, author-level linked entities).
5. Academic catalog linked entities + final EPFL assessment.
6. Validation and cache persistence.

## Token accounting

The repository analysis aggregates both:

- official API token usage (`input_tokens`, `output_tokens`), and
- estimated token usage (`estimated_input_tokens`, `estimated_output_tokens`).

These values are returned in `APIOutput.stats`.
