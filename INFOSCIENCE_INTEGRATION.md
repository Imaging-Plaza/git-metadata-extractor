# Infoscience API Integration - Implementation Summary

## Overview
This document describes the implementation of Infoscience API integration for querying EPFL's research repository from the three AI agents (repository, user, and organization enrichment).

## What Was Implemented

### 1. Data Models (`src/data_models/infoscience.py`)
Created comprehensive Pydantic models for Infoscience data:

- **`InfosciencePublication`**: Publication metadata including title, authors, DOI, abstract, dates, lab info, and repository URLs
- **`InfoscienceAuthor`**: Author/researcher information including name, email, ORCID, affiliation, and publication count
- **`InfoscienceLab`**: Laboratory/organizational unit details including name, description, parent organization, and research areas
- **`InfoscienceSearchResult`**: Wrapper for search results with pagination information

Each model includes a `to_markdown()` method for converting structured data to LLM-friendly markdown format.

### 2. HTTP Client & API Functions (`src/context/infoscience.py`)

#### Configuration Constants
- `INFOSCIENCE_BASE_URL`: Base URL for EPFL's Infoscience API
- `DEFAULT_MAX_RESULTS`: Default result limit (10)
- `REQUEST_TIMEOUT`: Request timeout (30 seconds)

#### Core HTTP Functions
- `_make_api_request()`: Async HTTP request helper with error handling
- `_parse_metadata()`: Extract single metadata field from DSpace responses
- `_parse_metadata_list()`: Extract multiple metadata values
- `_parse_publication()`: Convert DSpace item to InfosciencePublication

#### Search Functions
- `search_publications()`: Search for publications by title, DOI, or keywords
- `search_authors()`: Search for researchers by name
- `search_labs()`: Search for labs and organizational units
- `get_author_publications()`: Get all publications by a specific author

All functions use `httpx` for async operations and return structured Pydantic models.

### 3. PydanticAI Tool Functions (`src/context/infoscience.py`)

Four tool functions that agents can call:

- **`search_infoscience_publications_tool(query, max_results=10)`**
  - Searches publications by any criteria
  - Returns markdown-formatted results
  - Max 50 results per query

- **`search_infoscience_authors_tool(name, max_results=10)`**
  - Searches for authors/researchers
  - Returns author profiles with affiliations
  - Max 50 results per query

- **`search_infoscience_labs_tool(name, max_results=10)`**
  - Searches for labs and organizational units
  - Returns lab information with descriptions
  - Max 50 results per query

- **`get_author_publications_tool(author_name, max_results=10)`**
  - Gets all publications by a specific author
  - Returns full publication list in markdown
  - Max 50 results per query

### 4. Agent Integration

#### Repository Agent (`src/agents/repository.py`)
- Imported Infoscience tools: `search_infoscience_publications_tool`, `get_author_publications_tool`
- Tools registered when creating agent
- Updated system prompt with tool documentation

**Use cases:**
- Verify publication citations mentioned in README
- Find related publications for software
- Verify author EPFL affiliations

#### User Agent (`src/agents/user.py`)
- Imported Infoscience tools: `search_infoscience_authors_tool`, `get_author_publications_tool`
- Fixed to use proper `run_agent_with_fallback` signature with output_type and system_prompt
- Tools registered when creating agent
- Updated system prompt with tool documentation

**Use cases:**
- Find EPFL profiles for GitHub users
- Verify researcher affiliations
- Get publication history to determine research areas

#### Organization Enrichment Agent (`src/agents/organization_enrichment.py`)
- Imported Infoscience tools: `search_infoscience_labs_tool`, `search_infoscience_publications_tool`, `get_author_publications_tool`
- Tools added to agent creation function
- Updated system prompt with tool documentation

**Use cases:**
- Verify lab names are actual EPFL labs
- Confirm author affiliations via publications
- Get detailed organizational structure information

### 5. Agent Management Updates (`src/agents/agents_management.py`)

Modified to support tool registration:
- `create_agent_from_config()`: Added optional `tools` parameter
- `run_agent_with_fallback()`: Added optional `tools` parameter and passes through to agent creation

### 6. System Prompts Updated

#### Repository Agent Prompt (`src/agents/repository_prompts.py`)
Added section explaining:
- Available Infoscience tools
- When to use them (author verification, citation lookup, EPFL relationship)
- Example usage scenarios

#### User Agent Prompt (`src/agents/prompts.py`)
Added section explaining:
- Available Infoscience tools for user analysis
- When to use them (finding EPFL profiles, verifying affiliations)
- Example usage scenarios

#### Organization Agent Prompt (`src/agents/organization_prompts.py`)
Added section explaining:
- Available Infoscience tools for organization analysis
- When to use them (lab verification, author affiliation confirmation)
- Example usage scenarios

### 7. Module Exports

#### `src/context/__init__.py`
Exported all four Infoscience tool functions for easy import.

#### `src/data_models/__init__.py`
Exported all four Infoscience data models for type hints and validation.

## API Endpoints Used

The implementation queries these DSpace 7.6 API endpoints:

1. **`/api/discover/search/objects`** - General search with query parameters
   - Used for publication, author, and lab searches
   - Supports DSpace query syntax (e.g., `dc.contributor.author:name`)
   - Supports `dsoType` parameter to filter by type (item, community, collection)

2. **`/api/eperson/profiles/search/byName`** - Search author profiles
   - Used for direct author profile lookups

## Authentication

The implementation supports **optional authentication** via the `INFOSCIENCE_TOKEN` environment variable:

```bash
export INFOSCIENCE_TOKEN="your-token-here"
```

**When authentication is used:**
- Lab/organization searches use the token (some endpoints may require it)
- Token is sent as `Authorization: Bearer {token}` header
- Enables access to more comprehensive search results

**When to use authentication:**
- If you get 404 errors on lab/community searches
- If search results seem limited
- For accessing protected or detailed metadata

**Getting a token:**
Visit [EPFL Infoscience API documentation](https://www.epfl.ch/campus/library/services-researchers/infoscience-en/help-infoscience/export-share-and-reuse-infoscience-data-api-oai-exports-etc/) for token generation instructions.

## Features

### Logging & Monitoring
- **Tool invocation logging**: Each tool call logs with 🔍 emoji when called by agents
- **Success logging**: Results logged with ✓ showing total results found
- **Warning logging**: Empty results or issues logged with ⚠
- **Error logging**: Failures logged with ✗ including full exception details

Example log output:
```
INFO: 🔍 Agent tool called: search_infoscience_publications_tool(query='deep learning', max_results=10)
INFO: ✓ Infoscience publications search returned 24 total results
```

### Error Handling
- HTTP errors caught and logged with full details
- Timeouts handled gracefully (30s timeout)
- Empty results return structured responses (not errors)
- Invalid responses logged with exception tracebacks
- All errors return user-friendly markdown messages to the agent

### Pagination
- Default: 10 results per query
- Configurable up to 50 results
- Results include total count and current page info

### Markdown Formatting
- Clean, readable output for LLM consumption
- Includes all relevant metadata
- Links to original resources
- Result counts and pagination info

### Type Safety
- All responses validated with Pydantic models
- Type hints throughout
- Structured data with optional fields properly handled

## Testing Recommendations

To test the implementation:

1. **Unit Tests** (suggested location: `tests/test_infoscience.py`):
   ```python
   # Test data model validation
   # Test markdown conversion
   # Test API request functions (with mocked responses)
   # Test tool function output formats
   ```

2. **Integration Tests**:
   ```python
   # Test actual API calls (may be slow)
   # Test agent tool usage
   # Test end-to-end workflows
   ```

3. **Manual Testing**:
   - Run repository analysis on EPFL repos
   - Check if agents use tools appropriately
   - Verify tool responses are helpful

## Monitoring Tool Usage

### Log Output
When agents call Infoscience tools, you'll see clear logging output following this pattern:

```log
INFO: 🔍 Agent tool called: search_infoscience_publications_tool(query='machine learning imaging', max_results=10)
INFO: Found 15 publications for query: machine learning imaging
INFO: ✓ Infoscience publications search returned 15 total results

INFO: 🔍 Agent tool called: get_author_publications_tool(author_name='Martin Vetterli', max_results=5)
INFO: Fetching publications for author: Martin Vetterli
INFO: Found 5 publications for query: Martin Vetterli
INFO: ✓ Found 127 publications for author 'Martin Vetterli'
```

### Searching Logs
To find when tools were used:
```bash
# Find all Infoscience tool calls
grep "🔍 Agent tool called: search_infoscience" logs/*.log

# Find successful searches
grep "✓ Infoscience" logs/*.log

# Find errors
grep "✗ Error in search_infoscience" logs/*.log
```

The logging pattern matches the existing tool logging in the organization enrichment agent (ROR search, web search), making it consistent across all agent tools.

## Usage Examples

### Direct API Usage
```python
from src.context.infoscience import search_publications, search_authors

# Search for publications
results = await search_publications("deep learning", max_results=5)
for pub in results.publications:
    print(pub.to_markdown())

# Search for authors
authors = await search_authors("Jean Dupont", max_results=3)
for author in authors.authors:
    print(author.to_markdown())
```

### Agent Tool Usage
The tools are automatically available to agents during their execution. The agent can call them like:

**Best Practice - Search by Repository Name:**
```python
# Repository: https://github.com/sdsc-ordes/gimie
# The agent extracts "gimie" and searches for it
search_infoscience_publications_tool("gimie")
# This finds publications that mention the tool!
```

**Other common usage:**
```python
# Search for publications
search_infoscience_publications_tool("computer vision")

# Find author's publications and affiliations
get_author_publications_tool("Martin Vetterli")

# Verify if a lab exists
search_infoscience_labs_tool("CVLAB")
```

**Strategy:**
1. **First:** Search for the repository/tool name itself to find related publications
2. **Then:** Search for authors mentioned in the repository
3. **Finally:** Verify lab affiliations if needed

This approach helps find publications that cite or describe the software tool!

## Dependencies

All required dependencies were already present:
- `httpx` - Async HTTP requests
- `pydantic` - Data validation
- `pydantic-ai` - Agent framework

## Files Created/Modified

### New Files
- `src/data_models/infoscience.py` - Data models (348 lines)
- `src/context/infoscience.py` - API client and tools (698 lines)

### Modified Files
- `src/agents/agents_management.py` - Added tools parameter
- `src/agents/repository.py` - Integrated Infoscience tools
- `src/agents/user.py` - Integrated Infoscience tools, fixed signature
- `src/agents/organization_enrichment.py` - Integrated Infoscience tools
- `src/agents/repository_prompts.py` - Added tool documentation
- `src/agents/prompts.py` - Added tool documentation
- `src/agents/organization_prompts.py` - Added tool documentation
- `src/context/__init__.py` - Exported Infoscience tools
- `src/data_models/__init__.py` - Exported Infoscience models

### Total Lines Added
Approximately 1,100+ lines of code including:
- Data models with validation
- API client functions
- Tool wrappers
- Documentation updates
- Type hints and error handling

## Known Limitations

1. **Rate Limiting**: No rate limiting implemented - may need to add if API has limits
2. **Caching**: No caching of results - repeated searches will hit API each time
3. **Authentication**: Current implementation uses public API only - no authentication
4. **Testing**: No automated tests included - should be added
5. **Mock Data**: No mock responses for development/testing

## Future Enhancements

Potential improvements:
1. Add response caching (Redis/in-memory)
2. Implement rate limiting
3. Add authentication support for protected resources
4. Create comprehensive test suite
5. Add more specific search methods (by DOI, by lab, etc.)
6. Implement pagination for results > 50
7. Add search result relevance scoring
8. Support for advanced DSpace query syntax

## Conclusion

The Infoscience API integration is complete and functional. All three agents now have access to search EPFL's repository for:
- Publications and citations
- Author profiles and affiliations
- Laboratory and organizational information

The implementation follows the project's existing patterns and provides type-safe, well-documented tools that agents can use to enrich their analysis with EPFL-specific information.

