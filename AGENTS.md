# Agent Operating Guide for git-metadata-extractor

## Title + Purpose
This guide defines the operating contract for autonomous and semi-autonomous coding agents working in this repository.
The goal is safe, reproducible contributions with minimal human back-and-forth.

## Project Snapshot
- Language/runtime: Python project with package code under `src/`.
- Main runtime surfaces:
  - API: `src/api.py`
  - CLI: `src/main.py`
  - Analysis orchestration: `src/analysis/`
  - Agent pipelines: `src/agents/`
  - Data models/contracts: `src/data_models/`
  - Tests: `tests/`
- Core references:
  - `README.md`
  - `.internal/RISKS.md`
  - `docs/AGENT_STRATEGY.md`
  - `.internal/v2-plan/README.md`

## V2 Progress Track
- V2 work starts in `.internal/v2-plan/phase-0-tdd-foundation/`.
- Execute tasks in dependency order from `.internal/v2-plan/README.md`.
- V2B continuation work is tracked in `.internal/v2b-plan/` and executes in dependency order from `.internal/v2b-plan/README.md`.
- Current entry task: `.internal/v2b-plan/phase-6-regression-docs/P2B-27-e2e-golden-graph-regressions.md` (v2b Phase 5 tasks `P2B-24` through `P2B-26` complete and validated in scoped `tests/v2` runs).
- Phase 8 live-provider snapshot work is tracked separately in `.internal/phase-8/` (not part of the dependency graph in `.internal/v2-plan/README.md`).
- Canonical Infoscience IDs in v2 should resolve to `https://infoscience.epfl.ch/server/api/core/items/{uuid}` while accepting `entities/*` and `core/items/*` input forms.
- For schema promotion tasks, treat `dev/ontology-v2-json-response/a-001/json-schema/` as source artifacts and preserve byte-identical copies when promoting into `src/v2/schemas/`.
- In v2 repository-mode extracts, GitHub traversal is direct-only (source repo + direct owner + direct contributors). Keep ORCID/Infoscience/ROR enrichment enabled for discovered person/org entities.

## Environment & Prerequisites
Required environment variables (from `.env.dist` and `.env.example`):
- `OPENAI_API_KEY`
- `OPENROUTER_API_KEY`
- `GITHUB_TOKEN`
- `GITLAB_TOKEN`
- `INFOSCIENCE_TOKEN`
- `MODEL`
- `PROVIDER`
- `SELENIUM_REMOTE_URL`
- `CACHE_DB_PATH`
- `MAX_SELENIUM_SESSIONS`
- `MAX_CACHE_ENTRIES`
- `GUNICORN_CMD_ARGS`

Rules:
- Never print, log, or commit secrets.
- Never modify secret-bearing files (`.env`, `.env2`, similar secret files) unless explicitly asked.
- If required variables are missing for the requested task, fail fast and report exactly which variables are missing.
- For live-provider preflight/capture tasks, validate and report env var names only; never echo token values.
- Selenium checks require `SELENIUM_REMOTE_URL` when `selenium` is part of selected providers.
- For cache-bypass testing runs, use `V2_DISABLE_CACHE=true` (global for v2) or `/v2/extract?...&force_refresh=true` (per request).

## Canonical Commands
`justfile` is the source of truth for routine operations. Prefer `just` commands over ad-hoc shell commands when equivalent recipes exist.

- Setup:
  - `just install-dev`
  - `just setup`
- Run API:
  - `just serve-dev`
  - `just serve`
- Tests:
  - `just test`
  - `just test-file tests/<file>.py`
- Quality:
  - `just lint`
  - `just type-check`
  - `just check`
- CI-like local validation:
  - `just ci`
- V2 schema validation checks:
  - `python -m json.tool src/v2/schemas/strict/*.json`
  - `python -m json.tool src/v2/schemas/agent/*.json`
  - `pytest tests/v2/ --collect-only`
  - `pytest tests/v2 -m v2 --collect-only`
  - `just test-file tests/v2/test_test_infrastructure.py`
  - `just test-file tests/v2/test_promoted_strict_schemas.py`
  - `just test-file tests/v2/test_promoted_agent_schemas.py`
- Phase 8 live-provider checks:
  - `just preflight-live` (defaults to `github`, `ror`, `orcid`, `infoscience`, `logfire`, `selenium`)
  - `just capture-live`
  - `just test-live` (runs `pytest -m live_provider`)
  - `just test-offline`
  - `python scripts/v2/check_provider_connectivity.py --providers github ror orcid infoscience logfire` (optional: skip Selenium)

## Architecture Map For Agents
- Repository analysis flow entrypoints: `src/analysis/repositories.py`
- User and organization analysis entrypoints:
  - `src/analysis/user.py`
  - `src/analysis/organization.py`
- Agent implementations:
  - `src/agents/`
  - Atomic subpipeline: `src/agents/atomic_agents/`
- Data contracts:
  - `src/data_models/`
- Context and external lookups:
  - `src/context/`
- Cache layer:
  - `src/cache/`

## Editing Rules (Strict)
- Keep diffs minimal and scoped to the requested task.
- Preserve existing code style, project conventions, and import patterns.
- Do not rename or move public modules unless explicitly requested.
- Do not modify `.env`, `.env2`, or other secret-bearing files unless explicitly requested.
- Never run destructive git/file operations unless explicitly requested.
- If unrelated local changes exist, do not revert them; work around them and report context in the completion summary.
- Record newly discovered high-impact operational risks in `.internal/RISKS.md`.

## Task Playbooks
### Bug Fix Playbook
1. Reproduce the issue with a targeted test (or nearest equivalent validation).
2. Patch the minimal root cause.
3. Run focused tests first; run broader checks if shared paths were touched.
4. Report behavior change and residual risk.

### Feature Playbook
1. Identify API/data-model impact before coding.
2. Implement required model, pipeline, and endpoint wiring.
3. Add or adjust tests in `tests/`.
4. Validate with `just test` plus relevant lint/type checks.

### Refactor Playbook
1. Preserve behavior unless behavior change is explicitly requested.
2. Keep API contracts stable.
3. Prove parity with tests and checks.

## Testing & Validation Requirements
Minimum before completion:
- Run the nearest relevant tests.
- Run lint/type checks for touched Python modules when feasible.

If validation cannot be completed (missing dependencies, missing env vars, time constraints, external service constraints), report:
- What was attempted.
- What failed and why.
- The exact command(s) to run later.

## API/Schema Change Rules
For changes to FastAPI endpoints in `src/api.py`, include:
- Updated request/response behavior notes.
- Compatibility or migration notes.
- Test coverage for changed endpoint behavior.

For changes to models in `src/data_models/`, include:
- Impact notes on downstream usage (`src/analysis/`, `src/agents/`, API surface).
- Tests for new/changed fields and validation behavior.

## Output/Reporting Contract For Agents
Completion reports must include:
- Files changed
- Behavior change
- Commands run and key results
- Risks / follow-ups

No vague "done" messages. Reports must include verifiable evidence.

## Definition of Done
- Requested scope implemented.
- Relevant tests/checks passed, or blockers explicitly documented.
- No secret leakage.
- No unrelated mutations.
- No undocumented behavior changes.

## Test Cases & Scenarios For This Guide
1. Discoverability
- Scenario: A new agent opens the repository root.
- Expectation: `AGENTS.md` is present with quick-start commands and architecture map.

2. Fail-fast behavior
- Scenario: A task requires `OPENAI_API_KEY`, but it is missing.
- Expectation: Agent halts and reports the missing variable explicitly; no fabricated results.

3. Workflow consistency
- Scenario: Bug fix in `src/agents/organization_enrichment.py`.
- Expectation: Agent follows the bug-fix playbook and runs targeted tests first.

4. Safety guardrails
- Scenario: Dirty worktree with unrelated changes.
- Expectation: Agent avoids reverting unrelated files and reports context.

5. Reporting quality
- Scenario: Agent completes a task.
- Expectation: Final report includes changed files, commands, outcomes, and risks.

## Public API/Type Impact
- No code/API/type changes are introduced by this document.
- This file defines a repository-local agent policy contract only.
