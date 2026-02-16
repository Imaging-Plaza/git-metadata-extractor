# Git Metadata Extractor Documentation

Git Metadata Extractor analyzes Git repositories, GitHub users, and GitHub organizations, then enriches the output with LLM-based classification and academic catalog context.

## Documentation map

- [Getting Started](getting-started.md)
- [API and CLI](api-and-cli.md)
- [Design Notes](architecture/design-notes.md)
- [Repository Analysis Strategy](AGENT_STRATEGY.md)
- [Legacy Releases](releases/legacy-releases.md)

## System overview

```mermaid
flowchart LR
    A[Client or Automation] --> B[FastAPI app<br/>src/api.py]

    B --> C1[Repository analysis<br/>src/analysis/repositories.py]
    B --> C2[User analysis<br/>src/analysis/user.py]
    B --> C3[Organization analysis<br/>src/analysis/organization.py]

    C1 --> D[Atomic agents<br/>src/agents/atomic_agents]
    C2 --> D
    C3 --> D

    C1 --> E[Data models<br/>src/data_models]
    C2 --> E
    C3 --> E

    C1 --> F[Cache manager<br/>src/cache]
    C2 --> F
    C3 --> F

    D --> G[LLM provider]
    D --> H[Infoscience / ORCID / ROR / GitHub APIs]
```

## Versioning

- `dev` and `latest` track the `main` branch documentation.
- Tagged releases publish immutable doc versions.
- `stable` points to the newest released docs.
