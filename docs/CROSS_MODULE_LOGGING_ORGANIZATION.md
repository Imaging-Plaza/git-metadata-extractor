# Cross-Module Logging Organization Guide

## ✅ YES, All Logging Works Automatically!

Your codebase is **already correctly structured** for the enhanced logging system. No changes needed to your modules!

## How Python Logging Hierarchy Works

### The Root Logger Pattern

When you call `setup_logging()` in your main application:
```python
# In src/main.py or src/api.py startup
from utils.enhanced_logging import setup_logging
setup_logging(level=logging.INFO, use_colors=True)
```

This configures the **root logger**, which is the parent of ALL loggers in your application.

### Logger Hierarchy

All loggers created with `logging.getLogger(__name__)` inherit from the root:

```
root logger (configured by setup_logging())
├── src.api
├── src.main
├── src.core
│   ├── src.core.genai_model
│   ├── src.core.verification
│   ├── src.core.organization_enrichment
│   ├── src.core.users_parser
│   ├── src.core.cache
│   └── ...
└── src.utils
    ├── src.utils.utils
    └── ...
```

**All child loggers automatically inherit:**
- ✅ Formatters (colors, request IDs)
- ✅ Handlers (StreamHandler to stdout)
- ✅ Log levels
- ✅ Context variables (request IDs)

## Your Current Module Structure (Perfect!)

### Example 1: Verification Module
```python
# src/core/verification.py
import logging

logger = logging.getLogger(__name__)  # Creates "src.core.verification"

class Verification:
    def run(self):
        logger.info("Running metadata validation checks...")  # ← Includes request ID!
        # ...
        logger.warning(f"{len(self.issues)} validation issue(s) found.")  # ← Includes request ID!
```

### Example 2: GenAI Module
```python
# src/core/genai_model.py
import logging

logger = logging.getLogger(__name__)  # Creates "src.core.genai_model"

async def llm_request_repo_infos(repo_url: str):
    logger.info(f"Extracting metadata for {repo_url}")  # ← Includes request ID!
    # ...
    logger.error("OPENAI_API_KEY not found")  # ← Includes request ID!
```

### Example 3: Organization Enrichment
```python
# src/core/organization_enrichment.py
import logging

logger = logging.getLogger(__name__)  # Creates "src.core.organization_enrichment"

async def enrich_organizations_from_dict(llm_output, repository_url):
    logger.info(f"Enriching organizations for {repository_url}")  # ← Includes request ID!
    # ...
    logger.warning("⚠️ WARNING: CAPTCHA detected")  # ← Includes request ID!
```

## How Request IDs Propagate

### The Magic of ContextVar

Request IDs are stored in a `ContextVar`, which automatically propagates through:
- ✅ All async function calls in the same task
- ✅ All modules called from within the request context
- ✅ All nested function calls
- ✅ Error handlers and exception contexts

### Example Flow

```python
# In src/api.py
@app.post("/v1/extract")
async def extract_metadata(repo_url: str):
    async with AsyncRequestContext(prefix="api"):
        logger.info("API request received")  # [api-12345-d0f3] src.api

        # Call genai_model
        result = await llm_request_repo_infos(repo_url)
        # ↑ All logs in genai_model will have [api-12345-d0f3]

        # Call verification
        verification = Verification(result, repo_url)
        verification.run()
        # ↑ All logs in verification will have [api-12345-d0f3]

        # Call organization enrichment
        result = await enrich_organizations_from_dict(result, repo_url)
        # ↑ All logs in org_enrichment will have [api-12345-d0f3]

        logger.info("API request complete")  # [api-12345-d0f3] src.api
```

**Output:**
```
INFO [api-12345-d0f3] src.api: API request received
INFO [api-12345-d0f3] src.core.genai_model: Extracting metadata for repo
INFO [api-12345-d0f3] src.core.genai_model: Calling LLM API...
INFO [api-12345-d0f3] src.core.verification: Running metadata validation checks...
INFO [api-12345-d0f3] src.core.verification: Metadata is valid.
INFO [api-12345-d0f3] src.core.organization_enrichment: Enriching organizations
INFO [api-12345-d0f3] src.core.organization_enrichment: Searching ROR database...
INFO [api-12345-d0f3] src.api: API request complete
```

Notice: **Same request ID** `[api-12345-d0f3]` across ALL modules!

## Module Organization Benefits

### 1. **Clear Module Attribution**

Each log shows which module generated it:
```
INFO [api-d0f3] src.api: Request received
INFO [api-d0f3] src.core.genai_model: Calling LLM
INFO [api-d0f3] src.core.verification: Validating metadata
INFO [api-d0f3] src.core.organization_enrichment: Enriching organizations
```

You can immediately see:
- Which request (request ID)
- Which module (logger name)
- What happened (message)

### 2. **Easy Filtering by Module**

Filter logs by specific modules:
```bash
# See only verification logs
docker logs container | grep "src.core.verification"

# See only organization enrichment logs
docker logs container | grep "src.core.organization_enrichment"

# See only API layer logs
docker logs container | grep "src.api"

# See all core module logs
docker logs container | grep "src.core"
```

### 3. **Easy Filtering by Request**

Track a specific request across all modules:
```bash
# Follow request [api-12345-d0f3] through ALL modules
docker logs container | grep "\[api-12345-d0f3\]"
```

This shows the complete flow: api → genai_model → verification → organization_enrichment

### 4. **Combined Filtering**

Filter by both module AND request:
```bash
# See only organization enrichment for a specific request
docker logs container | grep "\[api-12345-d0f3\]" | grep "organization_enrichment"
```

## Module-Specific Log Levels (Optional)

If you want different log levels for different modules:

```python
# In setup_logging() or main.py startup
import logging

# Set root level to INFO
setup_logging(level=logging.INFO, use_colors=True)

# But make some modules more verbose
logging.getLogger("src.core.organization_enrichment").setLevel(logging.DEBUG)

# Or silence noisy modules
logging.getLogger("src.core.cache").setLevel(logging.WARNING)
```

This is useful for:
- Debugging specific modules without flooding logs
- Reducing noise from verbose third-party libraries
- Production vs development log levels

## Current Modules in Your Codebase

All these modules already work with enhanced logging:

```
✅ src.api                              - API endpoints
✅ src.main                             - Main application
✅ src.core.genai_model                 - LLM extraction
✅ src.core.verification                - Metadata validation
✅ src.core.organization_enrichment     - ROR/web enrichment
✅ src.core.users_parser                - GitHub user parsing
✅ src.core.orgs_parser                 - GitHub org parsing
✅ src.core.cache                       - Caching layer
✅ src.core.cache_manager               - Cache management
✅ src.core.cached_parsers              - Cached parsing
✅ src.core.gimie_methods               - GIMIE extraction
✅ src.utils.utils                      - Utility functions
```

**All automatically include request IDs and colors!**

## Testing Cross-Module Logging

Run the test to see it in action:
```bash
python test_cross_module_logging.py
```

This demonstrates:
- 3 concurrent API requests
- Each calling 4 different modules (api, genai_model, verification, organization_enrichment)
- All logs for each request have the same request ID
- All logs show which module generated them
- Colors make it easy to visually track each request

## Migration Checklist

- [x] Modules use `logging.getLogger(__name__)` ← **Already done!**
- [ ] Call `setup_logging()` once in main.py or app startup
- [ ] Add `AsyncRequestContext` to API endpoints
- [ ] (Optional) Set module-specific log levels

## Common Patterns

### Pattern 1: API Endpoint
```python
# In src/api.py
@app.post("/v1/extract")
async def extract_metadata(repo_url: str):
    async with AsyncRequestContext(prefix="extract"):
        logger.info(f"Request for {repo_url}")
        # All module calls here inherit the request ID
        result = await process_repository(repo_url)
        logger.info("Request complete")
        return result
```

### Pattern 2: Background Task
```python
# In src/utils/background_tasks.py
async def process_queue():
    async with AsyncRequestContext(prefix="queue"):
        logger.info("Processing queue item")
        # All logs here get [queue-PID-XXXX]
        await do_work()
```

### Pattern 3: Nested Contexts (if needed)
```python
# Main request
async with AsyncRequestContext(prefix="api"):
    logger.info("Main request")  # [api-12345-d0f3]

    # Sub-operation with its own ID
    async with AsyncRequestContext(prefix="sub"):
        logger.info("Sub-task")  # [sub-12345-a1b2]

    logger.info("Back to main")  # [api-12345-d0f3]
```

## Summary

### ✅ What You Already Have
- All modules use `logging.getLogger(__name__)` ← **Perfect!**
- Hierarchical logger names (src.core.verification, src.api, etc.)
- Consistent logging patterns across codebase

### ✅ What Works Automatically
- Request IDs propagate through ALL modules
- Colors apply to ALL modules
- Module names appear in ALL logs
- Filtering works for ANY module or request

### ✅ What You Need to Do
1. Call `setup_logging()` once at app startup
2. Add `AsyncRequestContext` to API endpoints
3. That's it!

### ✅ The Result
```
# 3 concurrent requests, each going through 4 modules
INFO [api-d0f3] src.api: Request 1
INFO [api-707e] src.api: Request 2
INFO [api-e295] src.api: Request 3
INFO [api-d0f3] src.core.genai_model: Request 1 extracting
INFO [api-707e] src.core.genai_model: Request 2 extracting
INFO [api-e295] src.core.genai_model: Request 3 extracting
INFO [api-d0f3] src.core.verification: Request 1 validating
INFO [api-707e] src.core.verification: Request 2 validating
INFO [api-e295] src.core.verification: Request 3 validating
INFO [api-d0f3] src.core.organization_enrichment: Request 1 enriching
INFO [api-707e] src.core.organization_enrichment: Request 2 enriching
INFO [api-e295] src.core.organization_enrichment: Request 3 enriching
```

**Clean, organized, and easy to follow!** 🎉
