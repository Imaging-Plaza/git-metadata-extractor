# Runnable examples

Run these commands **from the repository root**, after installing the project
with `just install-dev`. Use `python -m examples.<name>` so imports resolve
without modifying `sys.path`. Each example supports `--help`.

These examples use the current V2 API and provider interfaces. The old
`src.agents`, `core.cache_manager` and `utils.utils` enrichment APIs were removed
in 3.0.0.

| Example | Requirements | Result |
| --- | --- | --- |
| `example_caching` | Installed project; no network or credentials | Demonstrates cache reuse and refresh in `data/examples/cache.db` |
| `example_user_enrichment` | Running GME service and client `API_TOKEN` | Full extraction response in `data/examples/user.json` |
| `example_organization_enrichment` | Running GME service and client `API_TOKEN` | Full extraction response in `data/examples/organization.json` |
| `example_orcid_affiliations` | Internet access to the public ORCID API | Normalized provider record on stdout; cache in `data/examples/orcid.db` |

## Cache behavior without network access

```bash
python -m examples.example_caching
```

The fetch counters are `[1, 1, 2, 2]`: fetch once, reuse, refresh, then reuse
the refreshed entry. Each invocation uses a distinct demonstration key, so a
previous run cannot turn the first fetch into a hit. `--cache-path` changes the
example database location. No shared service cache is cleared.
The current `ProviderCache` defaults to a
30-day TTL. These examples explicitly choose paths under `data/examples/`;
the service uses `V2_PROVIDER_CACHE_PATH` (default `.cache/v2/providers.db`).
The legacy `data/api_cache.db` is a different schema and is not used here.

## User and organization extraction

Start the service using the [setup guide](../docs/getting-started.md). Export
`API_TOKEN` in the client shell with the same value configured on the server;
the example scripts do not load `.env`. Provider credentials such as
`GME_GITHUB_TOKEN` belong to the server configuration.

```bash
python -m examples.example_user_enrichment https://github.com/octocat --run-report
python -m examples.example_organization_enrichment https://github.com/sdsc-ordes --run-report
```

Both use `GET /v2/extract/{source_url}` and default to `rule_based`, which makes
no LLM calls. Set `--runtime hybrid` or `--runtime llm` to use the server's
configured model credentials and supporting services. For another server, use
`--api-url http://localhost:8000` or export `GME_API_URL`. `--output` changes the
destination; repeating a command overwrites that file.

The scripts validate the response with the project's Pydantic API model and
save the complete envelope: `output`, warnings, stats, extraction provenance,
and the optional `run_report`. By default, `output` is a v3 canonical JSON-LD
graph with separate profile nodes; it is not the old nested author structure.
An organization extraction can fan out across people and repositories. These
synchronous examples use a 600-second HTTP timeout; for longer jobs use
[`POST /v2/extract` and job polling](../docs/v2-api-reference.md).

## ORCID affiliations

```bash
python -m examples.example_orcid_affiliations https://orcid.org/0000-0002-1126-1535
python -m examples.example_orcid_affiliations 0000-0002-1126-1535 --refresh
```

This calls the public ORCID provider directly, without GME, an LLM, or the
separate index service. It returns employment and education records, including
available dates, roles and departments. The provider validates the ORCID
checksum, normalizes the URL, and caches successful records for 30 days.
`--refresh` bypasses the read and replaces that record in the example cache.
This is provider data, not an ontology graph; extraction applies the
[source mappings](../git_metadata_extractor/mapping/config/README.txt) and
validation stages to construct that graph.

## Offline verification

```bash
python -m pytest tests/v2/test_examples.py -q --no-cov
```

The tests exercise the actual cache and provider parsing with mocked HTTP
responses, check the V2 request/response contract and HTTP errors, and launch
all four `--help` commands without credentials. They do not call external APIs
or spend model tokens.
