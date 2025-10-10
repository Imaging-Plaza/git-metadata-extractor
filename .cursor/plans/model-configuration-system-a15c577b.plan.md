<!-- a15c577b-8dc6-4d03-8ff3-b6c4e48d5b84 d0755526-dc77-4303-8417-173bae94142e -->
# Model Configuration and Multi-Provider Support

## Overview

Implement a centralized model configuration system that allows configuring different providers and models for each analysis step (`run_llm_analysis`, `run_user_enrichment`, `run_organization_enrichment`) with automatic retry/fallback logic.

## Key Files to Modify

- **New file**: `src/llm/model_config.py` - Centralized configuration
- **New file**: `src/llm/repo_context.py` - Repository cloning and context generation logic
- **Modify**: `src/analysis/repositories.py` - Update analysis methods to use new config
- **Modify**: `src/llm/genai_model.py` - Refactor to PydanticAI with multi-provider support
- **Modify**: `src/agents/user_enrichment.py` - Use configurable models
- **Modify**: `src/agents/organization_enrichment.py` - Use configurable models

## Implementation Details

### 1. Create `src/llm/model_config.py`

Dictionary-based configuration structure:

```python
MODEL_CONFIGS = {
    "run_llm_analysis": [
        {
            "provider": "openai",
            "model": "gpt-4o",
            "max_retries": 3,
            "temperature": 0.2,
            "max_tokens": 16000,
            "timeout": 600.0,
        },
        {
            "provider": "openrouter",
            "model": "google/gemini-2.5-flash",
            "max_retries": 3,
            "temperature": 0.2,
            "max_tokens": 16000,
            "timeout": 300.0,
        },
        {
            "provider": "ollama",
            "model": "llama3.2",
            "base_url": "http://localhost:11434",
            "max_retries": 2,
            "temperature": 0.3,
            "timeout": 600.0,
        },
    ],
    "run_user_enrichment": [
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 8000,
            "timeout": 300.0,
        },
    ],
    "run_organization_enrichment": [
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "max_retries": 2,
            "temperature": 0.1,
            "max_tokens": 8000,
            "timeout": 300.0,
        },
    ],
}
```

Supported parameters per provider:

- **All providers**: `model`, `max_retries`, `temperature`, `timeout`
- **OpenAI/OpenRouter/OpenAI-compatible**: `max_tokens`, `top_p`, `frequency_penalty`, `presence_penalty`
- **Ollama**: `num_predict` (equivalent to max_tokens), `top_k`, `top_p`
- **OpenAI reasoning models (o3, o4)**: `max_completion_tokens` (instead of max_tokens), no temperature
- **OpenAI-compatible**: `base_url`, `api_key_env` (name of env var containing API key)
- **Ollama**: `base_url` (defaults to http://localhost:11434)

Environment variable override support:

- `LLM_ANALYSIS_MODELS` - JSON array for run_llm_analysis models
- `USER_ENRICHMENT_MODELS` - JSON array for run_user_enrichment models
- `ORG_ENRICHMENT_MODELS` - JSON array for run_organization_enrichment models

Provider configurations:

- **OpenAI**: Standard OpenAI API
- **OpenRouter**: Via openrouter.ai endpoint
- **OpenAI-compatible**: Custom base_url endpoint
- **Ollama**: Support both local (localhost:11434) and remote URLs

### 2. Refactor `src/llm/genai_model.py`

Convert `llm_request_repo_infos` to use PydanticAI Agent pattern:

- Create PydanticAI agent for repository analysis
- Implement multi-provider model initialization
- Add retry logic with exponential backoff (2s, 4s, 8s)
- Fallback to next model in list after max retries exceeded
- Keep existing helper functions (clone_repo, extract_git_authors, etc.)

### 3. Initialize Agents at Module Load Time

**`src/llm/genai_model.py`**:

- Read "run_llm_analysis" config at module initialization
- Create PydanticAI agent with first model from config
- Implement retry/fallback wrapper that tries models in sequence
- No changes needed to `repositories.py` - just calls the same function

**`src/agents/user_enrichment.py`**:

- Read "run_user_enrichment" config at module initialization
- Replace hardcoded `agent = Agent(model=f"openai:{os.getenv('MODEL')}")` with config-driven initialization
- Wrap agent.run() with retry/fallback logic
- `enrich_users()` and `enrich_users_from_dict()` remain unchanged

**`src/agents/organization_enrichment.py`**:

- Read "run_organization_enrichment" config at module initialization
- Replace hardcoded `agent = Agent(model=f"openai:{os.getenv('MODEL')}")` with config-driven initialization
- Wrap agent.run() with retry/fallback logic
- `enrich_organizations()` and `enrich_organizations_from_dict()` remain unchanged

This approach means:

- Configuration is loaded once when modules are imported
- No changes needed to `repositories.py` or function signatures
- Agents are automatically configured based on model_config.py or env vars
- Retry/fallback logic is transparent to callers

## Retry Strategy

For each model in the list:

1. Try up to `max_retries` times (default: 3)
2. Use exponential backoff between retries: 2^attempt seconds (2s, 4s, 8s)
3. On max retries exceeded, move to next model in list
4. If all models fail, raise exception with detailed error info

## Provider Support Details

### OpenAI

- Use `pydantic_ai` with model string: `openai:gpt-4o`
- API key from `OPENAI_API_KEY` env var

### OpenRouter

- Use `pydantic_ai` with custom HTTP client pointing to openrouter.ai
- API key from `OPENROUTER_API_KEY` env var

### OpenAI-compatible

- Use `pydantic_ai` with custom base_url
- Config: `{"provider": "openai-compatible", "base_url": "...", "api_key_env": "..."}`

### Ollama

- Support local: `http://localhost:11434`
- Support remote: custom URL from config
- Use `pydantic_ai` with model string: `ollama:llama3.2`
- Config: `{"provider": "ollama", "model": "llama3.2", "base_url": "http://localhost:11434"}`

## Clean Break from Old Approach

- Remove support for old `MODEL` and `PROVIDER` env vars (except for backwards compatibility during transition)
- All configuration comes from `model_config.py` or the new env var format (JSON arrays)
- Simplify code by removing old OpenAI client initialization logic
- Remove deprecated functions: `get_openrouter_response()`, `get_openai_response()` (sync versions)
- Clean up `genai_model.py` by removing old pattern code

### To-dos

- [ ] Create src/llm/model_config.py with dictionary-based configuration structure and env var override support
- [ ] Implement provider-specific helper functions for OpenAI, OpenRouter, OpenAI-compatible, and Ollama (local and remote) in model_config.py
- [ ] Refactor llm_request_repo_infos in genai_model.py to use PydanticAI Agent with multi-provider support and retry/fallback logic
- [ ] Update user_enrichment.py to support dynamic model configuration with retry/fallback logic
- [ ] Update organization_enrichment.py to support dynamic model configuration with retry/fallback logic
- [ ] Update repositories.py analysis methods to load and pass model configurations from model_config.py
