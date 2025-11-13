# Git Metadata Extractor - Developer Onboarding

This document provides a comprehensive overview of the `git-metadata-extractor` project, its architecture, and development conventions. It is intended for developers who are new to the project.

## 1. Project Overview

The Git Metadata Extractor is a tool designed to analyze software repositories, extract metadata, and enrich it using a series of AI-powered agents. The primary goal is to produce high-quality, structured metadata in JSON-LD format, aligned with schemas used by the EPFL Imaging Plaza and Open Pulse projects.

**Core Features:**
- Extracts basic metadata using `gimie`.
- Clones repositories to analyze content (READMEs, source code, configs).
- Uses a pipeline of AI agents (`pydantic-ai`) for analysis and enrichment.
- Enriches data with external sources like ORCID, ROR, and EPFL's Infoscience repository.
- Provides a final, holistic assessment of a repository's relationship with EPFL.
- Exposes functionality via a CLI and a FastAPI web server.

## 2. Architecture

The application follows a modular, pipeline-based architecture. The core logic is orchestrated by "analysis" classes (`Repository`, `User`, `Organization`) that execute a sequence of steps.

### Directory Structure

The `src/` directory is organized by function:

- `src/api.py`: FastAPI application entry point.
- `src/main.py`: CLI application entry point.
- `src/analysis/`: High-level orchestrators (`Repository`, `User`, `Organization` classes) that manage the analysis pipeline.
- `src/agents/`: Contains all AI agent logic. Each agent has a dedicated file and a `_prompts.py` file for its prompts.
- `src/data_models/`: The single source of truth for all Pydantic data models. All models are exported via `src/data_models/__init__.py`.
- `src/context/`: Provides data from external sources (e.g., `infoscience.py` for the EPFL academic catalog, `repository.py` for cloning and content extraction).
- `src/llm/`: Manages LLM configurations, allowing for multiple providers (OpenAI, OpenRouter, Ollama) with fallback mechanisms.
- `src/cache/`: Implements the SQLite-based caching system.
- `src/parsers/`: Handles parsing of raw data from sources like the GitHub API.

### Data Analysis Flow (Repository)

The analysis pipeline for a repository is a key concept:

1.  **Clone & Extract (`src/context/repository.py`):** The repository is cloned, and relevant files (code, docs, configs) are extracted into a markdown format.
2.  **GIMIE Analysis (`src/gimie_utils/`):** Basic metadata is extracted using `gimie`.
3.  **Initial LLM Analysis (`src/agents/repository.py`):** The main repository agent analyzes the extracted content to produce a `SoftwareSourceCode` Pydantic model.
4.  **Enrichment Stages:**
    - **ORCID Enrichment:** Author data is enriched with public ORCID information.
    - **User Enrichment (`src/agents/user_enrichment.py`):** Analyzes git authors and ORCID data to create detailed `Person` profiles.
    - **Organization Enrichment (`src/agents/organization_enrichment.py`):** Identifies and standardizes organizational affiliations using ROR.
    - **Academic Catalog Enrichment (`src/agents/academic_catalog_enrichment.py`):** Searches academic catalogs like Infoscience for related publications, authors, and labs.
5.  **Final EPFL Assessment (`src/agents/epfl_assessment.py`):** A final, holistic agent reviews all collected data to make a definitive, evidence-based assessment of the repository's relationship to EPFL, calculating a confidence score.
6.  **Validation & Caching:** The final, enriched data is validated against the Pydantic models and cached in the SQLite database.

## 3. Key Concepts & Patterns

### AI Agents (`pydantic-ai`)

- The core of the enrichment logic is built on `pydantic-ai`.
- **Schema Enforcement is CRITICAL:** Every agent call specifies a Pydantic model as its `output_type`. This forces the LLM to return structured, validated data. **Never use a generic `Dict` as an output type.**
- **Agent Organization:** Agents are located in `src/agents/`. Each has a main implementation file, a `_prompts.py` file, and may use tools from `src/agents/tools.py` or `src/context/`.
- **Structured Output:** Agents that search for multiple items (e.g., academic catalog) return structured dictionaries keyed by the exact input name, eliminating the need for fuzzy name matching in the Python code.

### Pydantic Models (`src/data_models/`)

- The project uses **Pydantic V2**.
- All data models are defined in `src/data_models/`. This provides a single source of truth for the application's data structures.
- **Type Discrimination:** A `type` field (`"Person"` or `"Organization"`) is used to distinguish between different entity types in mixed lists, such as the `author` field.
- **Field Naming:** Pydantic models use `camelCase` for field names to align with the target JSON-LD schema.
- **Validation:** Models include built-in validators (e.g., for ORCID and ROR IDs) to normalize data at the point of creation.

### Configuration (`src/llm/model_config.py`)

- The application supports multiple LLM providers (OpenAI, OpenRouter, Ollama, and any OpenAI-compatible endpoint).
- Configurations for each analysis type (e.g., `run_llm_analysis`, `run_user_enrichment`) are defined in `MODEL_CONFIGS`.
- Each configuration is a list of models, providing a fallback mechanism if the primary model fails.
- Configurations can be overridden at runtime using environment variables (e.g., `LLM_ANALYSIS_MODELS`).

### Token & Usage Tracking

- **Dual Tracking System:** The application tracks token usage in two ways:
    1.  **API-Reported:** Official token counts from the LLM provider's API response.
    2.  **Client-Side Estimation:** A fallback mechanism using `tiktoken` for validation and for models that don't report usage.
- **`APIStats` Model:** All API endpoints that perform analysis return a `stats` object containing detailed token counts, request duration, and status.
- **Accumulation:** The `Repository`, `User`, and `Organization` analysis classes accumulate token usage across all agent calls in an analysis pipeline.

## 4. Development Setup

### Prerequisites
- Python >= 3.9
- `just` (a command runner, `pip install just`)

### Installation Steps

1.  **Clone the repository.**
2.  **Install `uv`:** This project uses `uv` for fast dependency management.
    ```bash
    pip install uv
    ```
3.  **Set up the development environment:** This command will create a virtual environment, install all dependencies (including dev dependencies), and create a `.env` file from the template.
    ```bash
    just setup
    ```
4.  **Configure Environment Variables:** Edit the newly created `.env` file and add your API keys.
    ```env
    # Required
    OPENAI_API_KEY=sk-...
    OPENROUTER_API_KEY=sk-or-...
    GITHUB_TOKEN=ghp_...

    # Optional: For EPFL's internal model endpoint
    RCP_TOKEN=...
    ```
5.  **Install Pre-commit Hooks:** This is a **mandatory** step to ensure code quality.
    ```bash
    just pre-commit-install
    ```

## 5. Running the Application

### CLI Mode
For one-off analysis of a single repository.
```bash
# Basic usage
python src/main.py --url https://github.com/user/repo

# Using the just command
just extract https://github.com/user/repo
```

### API Server Mode
For development, run the FastAPI server with auto-reload.
```bash
# Start the development server
just serve-dev

# Access the API documentation
# Swagger UI: http://localhost:1234/docs
# ReDoc: http://localhost:1234/redoc
```

### Docker
The recommended way to run the application in production or for isolated development.
```bash
# Build the Docker image
just docker-build

# Run in development mode (with live code-reloading)
just docker-dev

# Run in production mode
just docker-run
```

## 6. Coding Standards & Conventions

### The Golden Rule: Pre-commit
**All code MUST pass pre-commit checks before being committed.** The hooks are installed via `just pre-commit-install` and will run automatically on `git commit`. You can also run them manually:
```bash
# Run all checks on all files
just pre-commit
```

### Linting & Formatting
- The project uses **Ruff** for both linting and formatting.
- Configuration is in `pyproject.toml`.
- **Format code:** `just format-ruff`
- **Check for linting issues:** `just lint`
- **Attempt to auto-fix issues:** `just lint-fix`

### Testing
- The project uses **Pytest**.
- Tests are located in the `tests/` directory.
- **Run all tests:** `just test`
- **Run tests with coverage:** `just test-coverage`

### Naming and Style
- Follow standard Python conventions (PEP8).
- **Modules:** `snake_case.py`
- **Classes:** `PascalCase`
- **Functions/Methods:** `snake_case`
- **Type Hints:** Use type hints for all function signatures.
- **Imports:** Use absolute imports from `src`. Ruff will handle sorting.
- **Pydantic Fields:** Use `camelCase` to match the JSON-LD schema.
