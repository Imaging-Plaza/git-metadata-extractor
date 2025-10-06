# Enhanced Logging for Concurrent Requests

## Problem

When multiple requests run concurrently, logs from different operations get interleaved, making it hard to:
- Follow a specific request's flow
- Distinguish between concurrent operations
- Debug issues in multi-request scenarios

**Example of confusing concurrent logs:**
```
INFO: Starting processing for repo A
INFO: Starting processing for repo B
INFO: Fetching metadata for repo A
INFO: Enriching organizations for repo B
INFO: Fetching metadata for repo B
ERROR: Error in enrichment for repo A  # Which repo had the error?
```

## Solution: Request IDs with Color Coding

Implemented an enhanced logging system that assigns each logical operation a unique, color-coded request ID that works across **multiple Gunicorn workers**.

### Key Features

1. **Unique Request IDs** - Each operation gets an ID like `[repo-12345-a3f2]` where `12345` is the worker PID
2. **Gunicorn-Compatible** - Request IDs include the process ID, ensuring uniqueness across workers
3. **Color Coding** - Each request ID has a consistent color throughout its lifecycle
4. **Context-Aware** - IDs automatically propagate through async operations within a worker
5. **Real-Time** - No buffering or aggregation delays
6. **Visual Separation** - Easy to follow specific requests visually

### How It Looks

```
2025-10-06 09:31:57 INFO [repo-17901-3183] __main__: Starting processing for user/repo-imaging
2025-10-06 09:31:57 INFO [repo-17902-763d] __main__: Starting processing for org/scientific-tool
2025-10-06 09:31:57 INFO [user-17901-a2a5] __main__: Starting user enrichment for john.doe
2025-10-06 09:31:57 INFO [repo-17901-3183] __main__: Fetching metadata for user/repo-imaging
2025-10-06 09:31:57 INFO [user-17901-a2a5] __main__: Fetching ORCID data for john.doe
2025-10-06 09:31:57 INFO [repo-17902-763d] __main__: Fetching metadata for org/scientific-tool
2025-10-06 09:31:57 WARNING [repo-17901-3183] __main__: Some issue in user/repo-imaging
```

**Notice:**
- `[repo-17901-3183]` and `[user-17901-a2a5]` are in **Worker 17901**
- `[repo-17902-763d]` is in **Worker 17902**
- Each has a unique color for easy visual tracking

## Implementation

### 1. Enhanced Logging Module

Created `/src/utils/enhanced_logging.py` with:

- `AsyncRequestContext` - Context manager for async operations
- `RequestContext` - Context manager for sync operations
- `set_request_id()` / `get_request_id()` - Manual ID management
- `ColoredFormatter` - Adds colors to logs
- Color-coded log levels (INFO=green, WARNING=yellow, ERROR=red)
- **Gunicorn compatibility** - Request IDs include process PID for uniqueness across workers

### Request ID Format

Request IDs follow the format: `[prefix-PID-XXXX]`

- **prefix**: Logical operation type (e.g., `repo`, `user`, `org`)
- **PID**: Process ID of the worker (ensures uniqueness across Gunicorn workers)
- **XXXX**: 4 random hex characters (ensures uniqueness within a worker)

Examples:
- `[repo-17901-3183]` - Repository processing in worker 17901
- `[user-17902-a2a5]` - User enrichment in worker 17902
- `[org-17901-eb5d]` - Organization enrichment in worker 17901

**Why include PID?**
With Gunicorn running multiple worker processes, each worker has its own memory space. Including the PID ensures that request IDs are globally unique across all workers, preventing confusion when aggregating logs.

### 2. Usage in Code

#### Basic Usage - Async

```python
from utils.enhanced_logging import AsyncRequestContext, setup_logging
import logging

# Setup once at application start
setup_logging(level=logging.INFO, use_colors=True)

logger = logging.getLogger(__name__)

async def process_repository(repo_url: str):
    # Each repository gets its own request ID
    async with AsyncRequestContext(prefix="repo"):
        logger.info(f"Starting processing for {repo_url}")
        # All logs within this context will have [repo-XXXX]
        await some_async_operation()
        logger.info("Processing complete")
```

#### In API Endpoints

```python
@app.post("/v1/extract")
async def extract_metadata(repo_url: str):
    async with AsyncRequestContext(prefix="extract"):
        logger.info(f"Extraction requested for {repo_url}")

        # ... your code ...

        logger.info("Extraction complete")
        return result
```

#### Organization Enrichment

```python
async def enrich_organizations_from_dict(llm_output, repository_url):
    async with AsyncRequestContext(prefix="org-enrich"):
        logger.info(f"Enriching organizations for {repository_url}")

        # All tool calls (search_ror, search_web, etc.)
        # will automatically include the request ID
        result = await agent.run(prompt, deps=context)

        logger.info("Enrichment complete")
        return result
```

#### Nested Contexts

```python
async with AsyncRequestContext(request_id="main-flow"):
    logger.info("Starting main workflow")  # [main-flow]

    # Sub-operations can have their own IDs
    async with AsyncRequestContext(prefix="sub"):
        logger.info("Sub-task processing")  # [sub-a1b2]

    logger.info("Continuing main flow")  # [main-flow] again
```

### 3. Comparison: Before vs After

#### Before (Confusing with Gunicorn)
```
2025-10-06 09:31:57 INFO __main__: Starting processing
2025-10-06 09:31:57 INFO __main__: Starting processing
2025-10-06 09:31:57 INFO __main__: Starting enrichment
2025-10-06 09:31:57 INFO __main__: Fetching metadata
2025-10-06 09:31:57 INFO __main__: Fetching metadata
2025-10-06 09:31:57 WARNING __main__: Some issue found
# Which worker? Which operation? Impossible to tell!
```

#### After (Clear with PID-based IDs)
```
2025-10-06 09:31:57 INFO [repo-17901-3183] __main__: Starting processing for repo A
2025-10-06 09:31:57 INFO [repo-17902-763d] __main__: Starting processing for repo B
2025-10-06 09:31:57 INFO [user-17901-a2a5] __main__: Starting enrichment for user X
2025-10-06 09:31:57 INFO [repo-17901-3183] __main__: Fetching metadata for repo A
2025-10-06 09:31:57 INFO [repo-17902-763d] __main__: Fetching metadata for repo B
2025-10-06 09:31:57 WARNING [repo-17901-3183] __main__: Some issue found
# Clear! Worker 17901, repo-3183 (repo A) had the issue
```

## Configuration

### Setup Logging

```python
from utils.enhanced_logging import setup_logging
import logging

# With colors (recommended for development)
setup_logging(level=logging.INFO, use_colors=True)

# Without colors (for production logs that go to files)
setup_logging(level=logging.INFO, use_colors=False)

# Debug level
setup_logging(level=logging.DEBUG, use_colors=True)
```

### Environment Variables

No special environment variables needed - just use the context managers!

## Benefits

### 1. **Visual Separation**
Each request has a unique color, making it easy to visually track through interleaved logs.

### 2. **Easy Filtering**
Filter logs by request ID, worker, or operation type:
```bash
# See all logs for a specific request
docker logs container | grep "\[repo-17901-3183\]"

# See all logs from a specific worker
docker logs container | grep "\[.*-17901-.*\]"

# See all repository processing
docker logs container | grep "\[repo-"

# See all organization enrichment
docker logs container | grep "\[org-"
```

### 3. **Better Debugging**
When an error occurs, you can immediately see all related logs by looking for the same request ID.

### 4. **No Performance Impact**
- Minimal overhead (just setting a context variable)
- No buffering or aggregation delays
- Real-time streaming of logs

### 5. **Automatic Propagation**
The request ID automatically propagates through:
- Async function calls within the same worker
- Tool calls in PydanticAI agents
- Nested operations
- Error handling

**Note:** Request IDs don't propagate across worker processes (by design), but each worker's requests are uniquely identifiable by the PID in the request ID.

## Gunicorn Multi-Worker Support

### How It Works

1. **Each worker process** gets its own PID (e.g., 17901, 17902, 17903)
2. **Request IDs include the PID** (e.g., `[repo-17901-3183]`, `[repo-17902-763d]`)
3. **Global uniqueness** across all workers is guaranteed
4. **Visual separation** still works - each unique request ID has a consistent color

### Example with 3 Gunicorn Workers

```bash
# Start Gunicorn with 3 workers
gunicorn -w 3 -k uvicorn.workers.UvicornWorker main:app
```

**Logs from 3 concurrent requests:**
```
INFO [repo-17901-3183] __main__: [Worker 1] Processing repo A
INFO [repo-17902-763d] __main__: [Worker 2] Processing repo B
INFO [repo-17903-0083] __main__: [Worker 3] Processing repo C
INFO [repo-17901-3183] __main__: [Worker 1] Metadata fetched for repo A
INFO [repo-17902-763d] __main__: [Worker 2] Metadata fetched for repo B
INFO [repo-17903-0083] __main__: [Worker 3] Metadata fetched for repo C
```

Notice how each worker (17901, 17902, 17903) has unique request IDs.

## Alternative Approaches Considered

### ❌ Aggregation (Buffering logs per request)
**Pros:** Clean per-request log blocks
**Cons:**
- Delays in seeing logs (must wait for request to complete)
- Memory overhead
- Harder to implement with streaming
- Not real-time

### ❌ Separate log files per request
**Pros:** Complete separation
**Cons:**
- File system overhead
- Hard to see overall system behavior
- Cleanup complexity
- Not suitable for containerized environments

### ✅ Request IDs with Colors (Chosen)
**Pros:**
- Real-time logging
- Easy visual tracking
- Minimal overhead
- Works with existing logging infrastructure
- Filterable and searchable
- No buffering or delays

**Cons:**
- Colors don't work in all log aggregation systems (can disable with `use_colors=False`)

## Migration Guide

### Step 1: Update Logging Setup

In your `main.py` or `api.py`:

```python
# OLD
from utils.logging_config import setup_logging
setup_logging()

# NEW
from utils.enhanced_logging import setup_logging
setup_logging(level=logging.INFO, use_colors=True)
```

### Step 2: Add Request Context to API Endpoints

```python
# Add to each endpoint
async def your_endpoint():
    async with AsyncRequestContext(prefix="endpoint-name"):
        # your code here
        pass
```

### Step 3: Add to Background Tasks

```python
async def background_task():
    async with AsyncRequestContext(prefix="task"):
        # your code here
        pass
```

## Testing

### Demo Script (Single Process)
Demo script available: `/workspaces/git-metadata-extractor/test_enhanced_logging.py`

```bash
python test_enhanced_logging.py
```

This demonstrates:
- Concurrent request logging
- Nested contexts
- Error handling with request IDs
- Color coding in action

### Gunicorn Simulation Test
Multi-worker test: `/workspaces/git-metadata-extractor/test_gunicorn_logging.py`

```bash
python test_gunicorn_logging.py
```

This simulates multiple Gunicorn workers (separate processes) and demonstrates:
- Request ID uniqueness across workers
- PID inclusion in request IDs
- Concurrent requests within each worker
- Cross-worker log interleaving with clear separation

## Files

- ✅ `/src/utils/enhanced_logging.py` - Enhanced logging implementation with Gunicorn support
- ✅ `/test_enhanced_logging.py` - Demo and testing script (single process)
- ✅ `/test_gunicorn_logging.py` - Multi-worker simulation test
- ✅ `/docs/ENHANCED_LOGGING_CONCURRENT_REQUESTS.md` - This documentation

## Future Enhancements

Potential improvements:
1. **Structured logging** - Add request ID to JSON logs
2. **Request duration tracking** - Automatic timing of requests
3. **Request correlation** - Link related requests (e.g., parent/child)
4. **Metrics integration** - Export metrics per request ID
5. **Distributed tracing** - Integration with OpenTelemetry

## Summary

The enhanced logging system solves concurrent request readability **with Gunicorn multi-worker support** by:
1. Assigning unique, color-coded IDs to each logical operation
2. **Including worker PID in request IDs** for global uniqueness across processes
3. Automatically propagating IDs through async contexts within each worker
4. Providing real-time, filterable logs
5. Maintaining minimal performance overhead

**Key advantage for Gunicorn:** Request IDs like `[repo-17901-3183]` clearly show which worker (17901) and which operation (3183) generated each log, making debugging multi-worker setups significantly easier!
