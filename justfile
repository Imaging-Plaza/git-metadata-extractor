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

# Install in development mode with all dependencies.
# The RAG-index layer lives in the open-pulse-sources repo; the v2 read-side
# providers import it as a library. It is a declared dependency of this
# project (pinned to an immutable tag in pyproject.toml), so the install
# below already brings it in — no separate pin to keep in sync.
# For cross-repo development, a checkout found either nested
# (./open-pulse-sources) or beside this repo (../open-pulse-sources) is then
# re-installed editable so local child edits take effect immediately.
install-dev:
    uv pip install -e ".[dev]"
    @if [ -d open-pulse-sources ]; then \
        echo "open-pulse-sources: nested checkout -> editable install"; \
        uv pip install -e ./open-pulse-sources; \
    elif [ -d ../open-pulse-sources ]; then \
        echo "open-pulse-sources: sibling checkout -> editable install"; \
        uv pip install -e ../open-pulse-sources; \
    else \
        echo "open-pulse-sources: using the pinned release from pyproject.toml"; \
    fi

# Set up development environment (install + create .env if needed)
setup:
    @echo "Setting up Git Metadata Extractor development environment..."
    pip install -e .
    @if [ ! -f .env ]; then cp .env.example .env; echo ".env created from .env.example — fill in API_TOKEN, GME_GITHUB_TOKEN and one LLM credential."; else echo ".env file already exists."; fi
    @echo "Setup complete!"

# ============================================================================
# Running the API Server
# ============================================================================

# Serve the FastAPI app in production mode
serve:
    uvicorn git_metadata_extractor.app:app --host {{HOST}} --port {{PORT}} --workers {{WORKERS}}

# Serve the FastAPI app in development mode with auto-reload
# Watches only the package for `.py` changes — keeps in-flight requests alive
# when extraction outputs, logs, or test files change.
serve-dev:
    uvicorn git_metadata_extractor.app:app --host {{HOST}} --port {{PORT}} --reload --reload-dir git_metadata_extractor --reload-include '*.py'

# Serve in development mode with debug logging
serve-dev-debug:
    LOG_LEVEL=DEBUG uvicorn git_metadata_extractor.app:app --host {{HOST}} --port {{PORT}} --reload --reload-dir git_metadata_extractor --reload-include '*.py' --log-level debug

# Serve with single worker (useful for debugging)
serve-single:
    uvicorn git_metadata_extractor.app:app --host {{HOST}} --port {{PORT}} --workers 1

# Serve using gunicorn (production-ready)
serve-gunicorn:
    gunicorn git_metadata_extractor.app:app --workers {{WORKERS}} --worker-class uvicorn.workers.UvicornWorker --bind {{HOST}}:{{PORT}}

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
    .venv/bin/python -m pytest tests/v2/ --cov=git_metadata_extractor --cov-report=html --cov-report=term -m 'not live_provider and not llm_integration'

# Run specific test file
test-file FILE:
    .venv/bin/python -m pytest {{FILE}} -v

# Run real-provider LLM integration tests only
test-llm-integration:
    .venv/bin/python -m pytest tests/v2/test_llm_repository_agent.py -m llm_integration -v

# Run tests in watch mode (requires pytest-watch)
test-watch:
    ptw tests/v2/

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

# Check committed v2 generated models are in sync with strict schemas.
# Uses `python` (PATH-resolved) so the recipe works in CI — where
# `setup-python` installs into the runner env, no `.venv/` — as well as
# in the devcontainer where `.venv/bin` is on PATH after activation. CI
# invokes this recipe via `just v2-models-check`.
v2-models-check:
    PYTHONPATH=. python scripts/v2/generate_v2_models.py --check

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
# Development Utilities
# ============================================================================

# Format code using black
format:
    black git_metadata_extractor/

# Format code using ruff
format-ruff:
    ruff format git_metadata_extractor/

# Lint code using ruff
lint:
    uv run ruff check git_metadata_extractor/

# Lint and fix issues automatically
lint-fix:
    uv run ruff check --fix git_metadata_extractor/

# Type check using mypy
type-check:
    uv run mypy git_metadata_extractor/

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
# RAG indices — moved to open-pulse-sources
# ============================================================================
# All index build/refresh tooling (ingest / embed / search / serve / reset,
# the federated CLI, and the /v2/indices/* management API) lives in
# https://github.com/sdsc-ordes/open-pulse-sources — see its justfile.
# The extraction service only *reads* the indices (Qdrant + DuckDB via the
# open_pulse_sources library).
