# Git Metadata Extractor - Task Runner
# Usage: just <command>

# Auto-load .env so API_TOKEN (and other vars) are visible to recipes.
set dotenv-load := true

# Default host and port (can be overridden with HOST=value PORT=value just serve)
HOST := env_var_or_default("HOST", "0.0.0.0")
PORT := env_var_or_default("PORT", "1234")
WORKERS := env_var_or_default("WORKERS", "4")

# Default recipe - show available commands
default:
    @just --list

# ============================================================================
# Installation & Setup
# ============================================================================

# Install dependencies from pyproject.toml
install:
    uv pip install .

# Install in development mode with all dependencies
install-dev:
    uv pip install -e ".[dev]"

# Set up development environment (install + create .env if needed)
setup:
    @echo "Setting up Git Metadata Extractor development environment..."
    pip install -e .
    @if [ ! -f .env ]; then echo "Creating .env file from template..."; echo "OPENAI_API_KEY=\nOPENROUTER_API_KEY=\nGITHUB_TOKEN=\nGITLAB_TOKEN=\nMODEL=gpt-4\nPROVIDER=openai\nCACHE_ENABLED=true" > .env; echo ".env file created. Please edit with your API keys."; else echo ".env file already exists."; fi
    @echo "Setup complete!"

# ============================================================================
# Running the API Server
# ============================================================================

# Serve the FastAPI app in production mode
serve:
    uvicorn src.api:app --host {{HOST}} --port {{PORT}} --workers {{WORKERS}}

# Serve the FastAPI app in development mode with auto-reload
# Watches only `src/` for `.py` changes — keeps in-flight requests alive
# when extraction outputs, logs, or test files change.
serve-dev:
    uvicorn src.api:app --host {{HOST}} --port {{PORT}} --reload --reload-dir src --reload-include '*.py'

# Serve in development mode with debug logging
serve-dev-debug:
    LOG_LEVEL=DEBUG uvicorn src.api:app --host {{HOST}} --port {{PORT}} --reload --reload-dir src --reload-include '*.py' --log-level debug

# Serve with single worker (useful for debugging)
serve-single:
    uvicorn src.api:app --host {{HOST}} --port {{PORT}} --workers 1

# Serve using gunicorn (production-ready)
serve-gunicorn:
    gunicorn src.api:app --workers {{WORKERS}} --worker-class uvicorn.workers.UvicornWorker --bind {{HOST}}:{{PORT}}

# Stop any uvicorn/gunicorn process listening on PORT.
# SIGTERM first; falls back to SIGKILL if anything is still bound after 2s.
serve-stop:
    #!/usr/bin/env bash
    set -u
    pids=$(lsof -ti :{{PORT}} 2>/dev/null || true)
    if [ -z "$pids" ]; then
        echo "no server listening on :{{PORT}}"
        exit 0
    fi
    echo "stopping pids on :{{PORT}}: $pids"
    kill $pids 2>/dev/null || true
    sleep 2
    pids=$(lsof -ti :{{PORT}} 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "force-killing: $pids"
        kill -9 $pids 2>/dev/null || true
    fi
    if lsof -i :{{PORT}} >/dev/null 2>&1; then
        echo "port :{{PORT}} still in use"; exit 1
    fi
    echo "port :{{PORT}} free"

# ============================================================================
# CLI Commands
# ============================================================================

# ============================================================================
# Docker Commands
# ============================================================================

# Build the Docker image
docker-build:
    docker build -t git-metadata-extractor -f tools/image/Dockerfile .

# Run the Docker container in production mode
docker-run:
    docker run -it --rm --env-file .env -p {{PORT}}:{{PORT}} --name git-metadata-extractor git-metadata-extractor

# Run the Docker container in development mode with volume mount
docker-dev:
    docker run -it --env-file .env -p {{PORT}}:{{PORT}} -v .:/app --entrypoint bash git-metadata-extractor

# Run the Docker container with bash entrypoint
docker-shell:
    docker run -it --env-file .env -p {{PORT}}:{{PORT}} -v .:/app --entrypoint bash git-metadata-extractor

# Start Selenium container for ORCID functionality
docker-selenium:
    docker run --rm -d -p 4444:4444 -p 7900:7900 --shm-size="2g" --name selenium-standalone-firefox selenium/standalone-firefox

# Stop Selenium container
docker-selenium-stop:
    docker stop selenium-standalone-firefox

# Build and run Docker container
docker-up: docker-build docker-run

# ============================================================================
# Testing
# ============================================================================

# Run fast local tests (impacted tests via testmon, no coverage, parallelized) TOKEN are provided empty to avoid trigering a real API call.
test:
    OPENAI_API_KEY= OPENROUTER_API_KEY= RCP_TOKEN= .venv/bin/python -m pytest tests/v2/ -q --testmon --no-cov -n auto --dist=loadfile

# Run full local tests (parallelized, deterministic selection)
test-full:
    .venv/bin/python -m pytest tests/v2/ -q -n auto --dist=loadfile -m 'not live_provider and not llm_integration'

# Run tests with coverage
test-coverage:
    .venv/bin/python -m pytest tests/v2/ --cov=src/v2 --cov-report=html --cov-report=term -m 'not live_provider and not llm_integration'

# Run specific test file
test-file FILE:
    .venv/bin/python -m pytest {{FILE}} -v

# Run real-provider LLM integration tests only
test-llm-integration:
    .venv/bin/python -m pytest tests/v2/test_llm_repository_agent.py -m llm_integration -v

# Run tests in watch mode (requires pytest-watch)
test-watch:
    PYTHONPATH=src ptw tests/v2/

# Run Phase 8 preflight connectivity checks against live providers
preflight-live:
    python scripts/v2/check_provider_connectivity.py

# Capture sanitized live provider snapshots and promote them into the live fixture namespace
capture-live:
    python scripts/v2/capture_provider_snapshots.py --promote-to tests/v2/fixtures/providers/live_snapshots

# Run opt-in live provider smoke tests
test-live:
    .venv/bin/python -m pytest tests/v2/test_live_provider_connectivity.py -m live_provider -v

# Run offline Phase 8 fixture/sanitizer checks
test-offline:
    .venv/bin/python -m pytest tests/v2/test_provider_connectivity_preflight.py tests/v2/test_provider_snapshot_sanitizer.py tests/v2/test_live_snapshot_fixture_contract.py -v

# Generate committed v2 Pydantic models from strict schemas
v2-models-generate:
    PYTHONPATH=. .venv/bin/python scripts/v2/generate_v2_models.py

# Check committed v2 generated models are in sync with strict schemas
v2-models-check:
    PYTHONPATH=. .venv/bin/python scripts/v2/generate_v2_models.py --check

# Run LLM repository agent end-to-end with real GIMIE context (no server needed)
# Usage: just v2-run-repo-agent sdsc-ordes/gimie
v2-run-repo-agent REPO:
    PYTHONPATH=. .venv/bin/python scripts/v2/run_llm_repository_agent.py {{REPO}}

# Run LLM repository + person agents (stages: context_gather -> repo_agent -> person_agents)
# Usage: just v2-run-repo-and-persons sdsc-ordes/gimie
v2-run-repo-and-persons REPO:
    PYTHONPATH=. .venv/bin/python scripts/v2/run_llm_repo_and_persons.py {{REPO}}

# Run full 7-stage LLM repository debug pipeline
# (context -> repo -> persons -> orgs -> articles -> memberships -> contributions)
# Usage: just v2-run-repo-persons-and-orgs sdsc-ordes/gimie
v2-run-repo-persons-and-orgs REPO:
    PYTHONPATH=. .venv/bin/python scripts/v2/run_llm_repo_persons_and_orgs.py {{REPO}}

# Alias for the full 7-stage LLM repository debug pipeline + link verification
# Usage: just v2-run-repo-full-llm sdsc-ordes/gimie
v2-run-repo-full-llm REPO:
    PYTHONPATH=. .venv/bin/python scripts/v2/run_llm_repo_persons_and_orgs.py {{REPO}} --verify-links

# ============================================================================
# Cache Management (via API)
# ============================================================================

# Get cache statistics
cache-stats:
    curl -X GET -H "Authorization: Bearer ${API_TOKEN}" http://localhost:{{PORT}}/v1/cache/stats | python -m json.tool

# Clean up expired cache entries
cache-cleanup:
    curl -X POST -H "Authorization: Bearer ${API_TOKEN}" http://localhost:{{PORT}}/v1/cache/cleanup | python -m json.tool

# Clear all cache entries
cache-clear:
    curl -X POST -H "Authorization: Bearer ${API_TOKEN}" http://localhost:{{PORT}}/v1/cache/clear | python -m json.tool

# Enable caching
cache-enable:
    curl -X POST -H "Authorization: Bearer ${API_TOKEN}" http://localhost:{{PORT}}/v1/cache/enable | python -m json.tool

# Disable caching
cache-disable:
    curl -X POST -H "Authorization: Bearer ${API_TOKEN}" http://localhost:{{PORT}}/v1/cache/disable | python -m json.tool

# ============================================================================
# Development Utilities
# ============================================================================

# Format code using black
format:
    black src/

# Format code using ruff
format-ruff:
    ruff format src/

# Lint code using ruff
lint:
    uv run ruff check src/

# Lint and fix issues automatically
lint-fix:
    uv run ruff check --fix src/

# Type check using mypy
type-check:
    uv run mypy src/

# Run all code quality checks
check: lint type-check
    @echo "All checks passed!"

# ============================================================================
# API Testing & Documentation
# ============================================================================

# Open the interactive API documentation (Swagger UI)
docs:
    @echo "Opening API documentation at http://localhost:{{PORT}}/docs"
    @if command -v xdg-open > /dev/null; then xdg-open http://localhost:{{PORT}}/docs; elif command -v open > /dev/null; then open http://localhost:{{PORT}}/docs; else echo "Please open http://localhost:{{PORT}}/docs in your browser"; fi

# ============================================================================
# Project Documentation Site (MkDocs + Mike)
# ============================================================================

# Serve project documentation locally with live reload
docs-serve:
    mkdocs serve

# Build project documentation and fail on warnings
docs-build:
    mkdocs build --strict

# List published documentation versions and aliases
docs-version-list:
    mike list

# Deploy docs from current branch as dev + latest aliases
docs-deploy-dev:
    mike deploy --push --branch gh-pages --update-aliases dev latest

# Deploy docs for a specific release version and update stable alias
docs-deploy-release VERSION:
    mike deploy --push --branch gh-pages --update-aliases {{VERSION}} stable

# Set the default docs version/alias
docs-set-default VERSION:
    mike set-default --push --branch gh-pages {{VERSION}}

# Test the main extract endpoint (was /v1/extract/json — that route is commented out;
# this hits the active /v1/repository/llm/json equivalent).
api-test-extract:
    curl -X GET -H "Authorization: Bearer ${API_TOKEN}" \
        "http://localhost:{{PORT}}/v1/repository/llm/json/https://github.com/qchapp/lungs-segmentation" | python -m json.tool

# Test the extract endpoint with force refresh
api-test-extract-refresh:
    curl -X GET -H "Authorization: Bearer ${API_TOKEN}" \
        "http://localhost:{{PORT}}/v1/repository/llm/json/https://github.com/qchapp/lungs-segmentation?force_refresh=true" | python -m json.tool

# Test the GIMIE endpoint
api-test-gimie:
    curl -X GET -H "Authorization: Bearer ${API_TOKEN}" \
        "http://localhost:{{PORT}}/v1/repository/gimie/json-ld/https://github.com/qchapp/lungs-segmentation" | python -m json.tool

# ============================================================================
# Cleanup
# ============================================================================

# Clean up Python cache files
clean-py:
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete
    find . -type f -name "*.pyo" -delete
    find . -type f -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

# Clean up test artifacts
clean-test:
    rm -rf .pytest_cache htmlcov .coverage

# Clean up all cache and temporary files
clean-all: clean-py clean-test
    rm -f api_cache.db test_output.json
    @echo "Cleaned up all cache and temporary files"

# ============================================================================
# Project Information
# ============================================================================

# Show project version
version:
    @python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"

# Show environment info
env-info:
    @echo "Python version:"
    @python --version
    @echo "\nPip version:"
    @pip --version
    @echo "\nInstalled packages:"
    @pip list | grep -E "(fastapi|uvicorn|gimie|pydantic|openai|google-genai)"

# Show project dependencies
deps:
    @python -c "import tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; print('\n'.join(deps))"

# ============================================================================
# Combined Workflows
# ============================================================================

# Complete development setup and start server
dev: setup serve-dev

# # Run checks and tests before committing
# pre-commit: lint test
#     @echo "✓ All pre-commit checks passed!"

# Full CI pipeline (lint, type-check, test)
ci: lint type-check test-coverage
    @echo "✓ CI pipeline completed successfully!"

# ============================================================================
# Pre-commit Commands
# ============================================================================

# Install pre-commit hooks
pre-commit-install:
    pre-commit install

# Install pre-commit hooks for commit-msg
pre-commit-install-msg:
    pre-commit install --hook-type commit-msg

# Run pre-commit on all files
pre-commit:
    pre-commit run --all-files

# Run pre-commit on staged files only
pre-commit-staged:
    pre-commit run

# Update pre-commit hooks to latest versions
pre-commit-update:
    pre-commit autoupdate

# Clean pre-commit cache
pre-commit-clean:
    pre-commit clean

# ============================================================================
# Infoscience indexer (src/index/infoscience)
# ============================================================================

# Solr fulltext discover for configured filter terms.
index-infoscience-discover *ARGS:
    .venv/bin/python -m src.index.infoscience discover {{ARGS}}

# Download TEXT bundle plaintext for each discovered item.
index-infoscience-fetch-text *ARGS:
    .venv/bin/python -m src.index.infoscience fetch-text {{ARGS}}

# Regex-extract GitHub/HuggingFace URLs from fetched text.
index-infoscience-extract-matches:
    .venv/bin/python -m src.index.infoscience extract-matches

# Pull Person/Org authority UUIDs from matched articles.
index-infoscience-extract-relations:
    .venv/bin/python -m src.index.infoscience extract-relations

# Fetch raw JSON for each linked Person/OrgUnit.
index-infoscience-fetch-related *ARGS:
    .venv/bin/python -m src.index.infoscience fetch-related {{ARGS}}

# Chunk + embed + populate LanceDB tables.
index-infoscience-embed *ARGS:
    .venv/bin/python -m src.index.infoscience embed {{ARGS}}

# Hybrid query (filter → vector → rerank). Pass the query as the first arg.
index-infoscience-query QUERY *ARGS:
    .venv/bin/python -m src.index.infoscience query "{{QUERY}}" {{ARGS}}

# Ingest the on-disk raw/* JSON tree into the infoscience DuckDB store.
# Optional: --links-dump=<path> to merge a dump_link_articles.py output
# into the article_links table.
index-infoscience-ingest-duckdb *ARGS:
    .venv/bin/python -m src.index.infoscience ingest-duckdb {{ARGS}}

# Show pipeline + LanceDB counts and paths.
index-infoscience-status:
    .venv/bin/python -m src.index.infoscience status

# ============================================================================
# OpenAlex indexer (src/index/openalex)
# ============================================================================

# Pull OpenAlex entities into DuckDB. Pass --scope epfl|switzerland.
openalex-ingest *ARGS:
    .venv/bin/python -m src.index.openalex.cli ingest {{ARGS}}

# Discover Swiss/EPFL Works mentioning github.com URLs (test set for v2).
openalex-find-github *ARGS:
    .venv/bin/python -m src.index.openalex.cli find-github {{ARGS}}

# Embed DuckDB rows into Qdrant via the RCP embedding endpoint.
openalex-embed *ARGS:
    .venv/bin/python -m src.index.openalex.cli embed {{ARGS}}

# Re-push existing DuckDB chunks into Qdrant (re-embeds chunks.text via RCP).
# Use after a Qdrant wipe — does NOT modify DuckDB.
openalex-rebuild-qdrant *ARGS:
    .venv/bin/python -m src.index.openalex.cli rebuild-qdrant {{ARGS}}

# Semantic retrieval (vector + rerank). First positional is the query.
openalex-search QUERY *ARGS:
    .venv/bin/python -m src.index.openalex.cli search "{{QUERY}}" {{ARGS}}

# Read-only SQL over the DuckDB dump (predefined or guarded ad-hoc).
openalex-query *ARGS:
    .venv/bin/python -m src.index.openalex.cli query {{ARGS}}

# Run the FastAPI app.
openalex-serve *ARGS:
    .venv/bin/python -m src.index.openalex.cli serve {{ARGS}}

# Run the OpenAlex test suite only.
openalex-test:
    .venv/bin/python -m pytest tests/index/openalex/ -v -m openalex

# ============================================================================
# ORCID indexer (src/index/orcid)
# ============================================================================

# Build the seed ORCID list (OpenAlex authors + ORCID expanded-search).
# Pass --scope epfl|switzerland and optionally --source openalex|orcid_search|both.
orcid-discover *ARGS:
    .venv/bin/python -m src.index.orcid discover {{ARGS}}

# Fetch full ORCID records for seeded IDs, post-filter, persist to DuckDB.
orcid-ingest *ARGS:
    .venv/bin/python -m src.index.orcid ingest {{ARGS}}

# Chunk + embed in-scope rows, push to Qdrant via the RCP embedding endpoint.
orcid-embed *ARGS:
    .venv/bin/python -m src.index.orcid embed {{ARGS}}

# Semantic retrieval (vector + RCP rerank). First positional is the query.
orcid-search QUERY *ARGS:
    .venv/bin/python -m src.index.orcid search "{{QUERY}}" {{ARGS}}

# Read-only SQL over the ORCID DuckDB (predefined or guarded ad-hoc).
orcid-query *ARGS:
    .venv/bin/python -m src.index.orcid query {{ARGS}}

# Show counts + paths for the chosen scope.
orcid-status *ARGS:
    .venv/bin/python -m src.index.orcid status {{ARGS}}

# Run the FastAPI app on port 8002 by default.
orcid-serve *ARGS:
    .venv/bin/python -m src.index.orcid serve {{ARGS}}

# Run the ORCID test suite only.
orcid-test:
    .venv/bin/python -m pytest tests/index/orcid/ -v

# ============================================================================
# HuggingFace indexer (src/index/huggingface)
# ============================================================================

# Pull HuggingFace metadata + cards into DuckDB. Pass --scope epfl|switzerland
# and optionally --types models,datasets,spaces.
hf-ingest *ARGS:
    .venv/bin/python -m src.index.huggingface ingest {{ARGS}}

# Substring-search the Hub for unknown EPFL/Swiss orgs; writes candidates to
# logs/discover_orgs.jsonl for human review (never auto-promotes to seed).
hf-discover-orgs *ARGS:
    .venv/bin/python -m src.index.huggingface discover-orgs {{ARGS}}

# Chunk + embed cards, push vectors to Qdrant via the RCP embedding endpoint.
hf-embed *ARGS:
    .venv/bin/python -m src.index.huggingface embed {{ARGS}}

# Semantic retrieval (vector + RCP rerank). First positional is the query.
hf-search QUERY *ARGS:
    .venv/bin/python -m src.index.huggingface search "{{QUERY}}" {{ARGS}}

# Read-only SQL over the HuggingFace DuckDB (predefined or guarded ad-hoc).
hf-query *ARGS:
    .venv/bin/python -m src.index.huggingface query {{ARGS}}

# Show DuckDB row counts + Qdrant collection size + paths.
hf-status:
    .venv/bin/python -m src.index.huggingface status

# Run the FastAPI app (default port 8002).
hf-serve *ARGS:
    .venv/bin/python -m src.index.huggingface serve {{ARGS}}

# Run the HuggingFace test suite only.
hf-test:
    .venv/bin/python -m pytest tests/index/huggingface/ -v

# ============================================================================
# Zenodo indexer (src/index/zenodo)
# ============================================================================

# Pull Zenodo records into DuckDB. Pass --scope epfl|switzerland.
zenodo-ingest *ARGS:
    .venv/bin/python -m src.index.zenodo ingest {{ARGS}}

# Chunk + embed records, push vectors to Qdrant via the RCP embedding endpoint.
zenodo-embed *ARGS:
    .venv/bin/python -m src.index.zenodo embed {{ARGS}}

# Semantic retrieval (vector + RCP rerank). First positional is the query.
zenodo-search QUERY *ARGS:
    .venv/bin/python -m src.index.zenodo search "{{QUERY}}" {{ARGS}}

# Read-only SQL over the Zenodo DuckDB (predefined or guarded ad-hoc).
zenodo-query *ARGS:
    .venv/bin/python -m src.index.zenodo query {{ARGS}}

# Show DuckDB row counts + Qdrant collection size + paths.
zenodo-status:
    .venv/bin/python -m src.index.zenodo status

# Run the FastAPI app (default port 8003).
zenodo-serve *ARGS:
    .venv/bin/python -m src.index.zenodo serve {{ARGS}}

# ============================================================================
# EPFL Graph disciplines indexer (src/index/epfl_graph)
# ============================================================================
# RAG index over the curated EPFL Graph academic ontology (~2226 categories,
# 6 levels deep, each backed by 50-110 anchor Wikipedia concepts). Requires
# EPFL_GRAPH_USERNAME / EPFL_GRAPH_PASSWORD for ingest, RCP_TOKEN for embed.

# Walk the ontology tree and persist categories to DuckDB.
epfl-graph-ingest *ARGS:
    .venv/bin/python -m src.index.epfl_graph ingest {{ARGS}}

# Embed categories and push them into Qdrant collection `epfl_graph_disciplines`.
epfl-graph-embed *ARGS:
    .venv/bin/python -m src.index.epfl_graph embed {{ARGS}}

# Semantic retrieval over the disciplines index. First positional is the query.
epfl-graph-search QUERY *ARGS:
    .venv/bin/python -m src.index.epfl_graph search "{{QUERY}}" {{ARGS}}

# DuckDB row counts + Qdrant collection name + paths.
epfl-graph-status:
    .venv/bin/python -m src.index.epfl_graph status

# ============================================================================
# SWISSUbase indexer (src/index/swissubase)
# ============================================================================
# SWISSUbase has no public REST API — every catalogue endpoint requires
# the SPA's session cookie. Ingest drives a Selenium browser session and
# calls the JSON endpoints from inside it, so SELENIUM_REMOTE_URL must
# be set. Default scope `epfl_sdsc_ethz` ingests everything but only
# embeds studies whose institution string matches EPFL / ETHZ / SDSC.

# Drive the catalogue via Selenium and persist studies/persons/institutions.
# Pass --scope epfl_sdsc_ethz|switzerland and --limit N for smoke runs.
swissubase-ingest *ARGS:
    .venv/bin/python -m src.index.swissubase ingest {{ARGS}}

# Chunk + embed in-scope entities, push vectors to Qdrant.
# Pass --entity studies|datasets|persons|institutions to restrict.
swissubase-embed *ARGS:
    .venv/bin/python -m src.index.swissubase embed {{ARGS}}

# Semantic retrieval (vector + RCP rerank). First positional is the query.
swissubase-search QUERY *ARGS:
    .venv/bin/python -m src.index.swissubase search "{{QUERY}}" {{ARGS}}

# Read-only SQL over the SWISSUbase DuckDB (predefined or guarded ad-hoc).
swissubase-query *ARGS:
    .venv/bin/python -m src.index.swissubase query {{ARGS}}

# Show DuckDB row counts + Qdrant collection size + paths.
swissubase-status:
    .venv/bin/python -m src.index.swissubase status

# Run the FastAPI app (default port 8004).
swissubase-serve *ARGS:
    .venv/bin/python -m src.index.swissubase serve {{ARGS}}

# ============================================================================
# RenkuLab indexer (src/index/renkulab)
# ============================================================================

# Pull RenkuLab projects/groups/users/data_connectors into DuckDB.
# Pass --scope all|epfl|switzerland and optionally --only entity1,entity2.
renku-ingest *ARGS:
    .venv/bin/python -m src.index.renkulab ingest {{ARGS}}

# Chunk + embed entities, push vectors to Qdrant via the RCP embedding endpoint.
# Pass --entities projects,groups,users,data_connectors to restrict (default: all).
renku-embed *ARGS:
    .venv/bin/python -m src.index.renkulab embed {{ARGS}}

# Semantic retrieval (vector + RCP rerank). First positional is the query.
renku-search QUERY *ARGS:
    .venv/bin/python -m src.index.renkulab search "{{QUERY}}" {{ARGS}}

# Read-only SQL over the RenkuLab DuckDB (predefined or guarded ad-hoc).
renku-query *ARGS:
    .venv/bin/python -m src.index.renkulab query {{ARGS}}

# Show DuckDB row counts + Qdrant collection sizes + paths.
renku-status:
    .venv/bin/python -m src.index.renkulab status

# Run the FastAPI app (default port 8004).
renku-serve *ARGS:
    .venv/bin/python -m src.index.renkulab serve {{ARGS}}

# ============================================================================
# GitHub repository indexer (src/index/github)
# ============================================================================

# Fetch GitHub repo metadata + README into DuckDB. Pass --scope epfl|switzerland,
# optionally --repos owner/name,... and/or --from-openalex.
gh-ingest *ARGS:
    .venv/bin/python -m src.index.github ingest {{ARGS}}

# Chunk + embed repos, push vectors to Qdrant via the RCP embedding endpoint.
gh-embed *ARGS:
    .venv/bin/python -m src.index.github embed {{ARGS}}

# Recovery path: re-derive Qdrant points from the existing chunks table.
# Use after a Qdrant wipe (instead of `gh-embed`, which would skip everything).
gh-rebuild-qdrant:
    .venv/bin/python -m src.index.github rebuild-qdrant

# Semantic retrieval (vector + RCP rerank). First positional is the query.
gh-search QUERY *ARGS:
    .venv/bin/python -m src.index.github search "{{QUERY}}" {{ARGS}}

# Read-only SQL over the GitHub DuckDB (predefined or guarded ad-hoc).
gh-query *ARGS:
    .venv/bin/python -m src.index.github query {{ARGS}}

# Show DuckDB row counts + Qdrant collection size + paths.
gh-status:
    .venv/bin/python -m src.index.github status

# Run the FastAPI app (default port 8004).
gh-serve *ARGS:
    .venv/bin/python -m src.index.github serve {{ARGS}}

# ============================================================================
# Federated cross-index layer (src/index/_federated)
# ============================================================================

# Federated semantic search across every registered index in parallel.
# Pass `--indices huggingface,openalex` to scope; `--filter k=v` (repeatable);
# `--entity-type X` to restrict each adapter to one type.
gme-search QUERY *ARGS:
    .venv/bin/python -m src.index._federated search "{{QUERY}}" {{ARGS}}

# Cross-index entity lookup. Pass any identifier — slug, URL, ORCID, ROR,
# DOI, UUID — and every adapter that recognises it returns matches.
gme-entity ID *ARGS:
    .venv/bin/python -m src.index._federated entity "{{ID}}" {{ARGS}}

# List registered adapters and the entity types each exposes.
gme-indices:
    .venv/bin/python -m src.index._federated indices

# Walk the HF base_models DAG (ancestors + descendants) from a repo_id.
hf-lineage REPO_ID *ARGS:
    .venv/bin/python -m src.index.huggingface lineage "{{REPO_ID}}" {{ARGS}}
