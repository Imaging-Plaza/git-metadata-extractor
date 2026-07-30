# Estimated Token Tracking - Current Status

Token accounting is now implemented as a first-class part of analysis outputs.

## What is tracked

Each analysis pipeline accumulates both:

- official API token counts (`input_tokens`, `output_tokens`), and
- estimated token counts (`estimated_input_tokens`, `estimated_output_tokens`).

These are aggregated into `APIStats` (`src/data_models/api.py`) and returned in endpoint responses.

## Where aggregation happens

- Repository pipeline: `src/analysis/repositories.py`
- User pipeline: `src/analysis/user.py`
- Organization pipeline: `src/analysis/organization.py`

Each stage updates cumulative counters when `usage` metadata is present.

## Why estimated tokens still matter

Some model/provider paths may not always return complete usage metadata. Estimated counts provide a fallback metric for observability and cost tracking.

## Operational check

When validating new agents/stages, confirm both official and estimated counters increase in the endpoint `stats` payload.
