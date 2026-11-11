# Git Metadata Extractor

Turn a GitHub repository, user or organization URL into a research graph:
software, people, organizations, affiliations and publications. The FastAPI
service combines deterministic source lookups, indexed research and optional
LLM agents. Default output follows the **pinned v3 development ontology**;
the HTTP API remains `/v2`.

[Open the visual field guide](guide/index.html){ .md-button .md-button--primary }
[Get started](getting-started.md){ .md-button }

The field guide provides offline C4 diagrams, source boundaries, controlled
extraction animations, mapping recipes and ontology maintenance. Animations
start only when requested and respect reduced motion.

## Start with the right view

| Guide | What you will learn |
|---|---|
| [Visual field guide](guide/index.html) | C4 views, sources and animated sequences |
| [Getting started](getting-started.md) | Installation and first request |
| [Architecture overview](architecture/overview.md) | Responsibilities, lifecycle and caches |
| [Mapping and ontology maintenance](architecture/maintenance.md) | YAML, semantic fixtures, contracts and run reports |
| [V2 extraction pipeline](v2-pipeline.md) | Generation plans, post-agent chains and gates |
| [Cross-repo contract](cross-repo-contract.md) | Version compatibility and shared stores |

## Two cooperating services

**Git Metadata Extractor** owns extraction, jobs, generation, reconciliation,
ontology projection and validation. There are two sequencers:
`pipeline/orchestrator.py` chooses input-specific generation plans;
`pipeline/run.py` defines post-agent stage chains.

**[open-pulse-sources](https://github.com/sdsc-ordes/open-pulse-sources)** owns
index ingest, embedding and reset. The extractor imports its pinned read-side
library and still shares Qdrant and DuckDB with the writer. Optional HTTP
search/manifest access does not mean that all record hydration is remote.
The library `v0.1.2` and writer image `0.1.2` must stay aligned.

Vectors help find candidates; records provide facts. Embedding models encode
retrieval text. Extraction LLMs reason over context and tools. These are
different responsibilities and configuration choices.

## Choose a runtime

| Runtime | Behavior |
|---|---|
| `rule_based` | Deterministic generation and resolution; LLM-free |
| `hybrid` | Rule-based generation, typed refiners and enabled affiliation resolution |
| `llm` | Per-entity model agents and enabled LLM-only stages |

The [visual extraction sequence](guide/index.html#extraction) compares these
paths. Request `include_run_report=true` for observed execution and known
usage subtotals beside `output`. A pipeline cache hit reports `cache_hit`
without inventing new execution. See
[report interpretation](architecture/maintenance.md#observing-an-extraction).

## Use, operate and extend

| Documentation | Scope |
|---|---|
| [V2 API reference](v2-api-reference.md) | Requests, jobs, authentication and responses |
| [API and CLI](api-and-cli.md) | Quick command reference |
| [Repository enrichment fields](repository-enrichment-fields.md) | Repository metadata |
| [Operations runbook](OPERATIONS_RUNBOOK.md) | Running the service |
| [V2 agent RAG tools](v2-rag-tools.md) | Read-side tools |
| [Concept tagging](concept-tagging.md) | Optional concepts and disciplines |
| [Design notes](architecture/design-notes.md) | Architecture decisions |
| [Roadmap](ROADMAP.md) | Planned work |
| [V1 migration](migration-v1-to-v2.md) | Removed endpoints and replacements |
| [Archive](archive/index.md) | Historical working notes |

The [historical v3 proposal](releases/ontology-v3.0.0.md) preserves an earlier
design. Use the vendor pin and generated contracts for current ontology work.

## Documentation versions

- `dev` and `latest` track the `main` branch documentation.
- Tagged releases publish immutable documentation versions.
- `stable` points to the newest released documentation.
