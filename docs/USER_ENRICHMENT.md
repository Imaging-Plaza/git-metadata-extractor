# User Enrichment Module

The `user_enrichment.py` module provides functionality to enrich author/user information from repository metadata, with a particular focus on identifying and tracking their affiliations over time.

## Overview

This module uses PydanticAI agents to perform intelligent analysis of repository authors by:
- Analyzing git author information (names, emails, commit patterns)
- Querying ORCID API for academic profiles
- Searching the web (DuckDuckGo) for additional context
- Inferring affiliations from email domains
- Tracking temporal affiliation patterns based on commit history

## Key Features

### 1. **Multi-Source Enrichment**
- Email domain analysis for institutional affiliations
- ORCID record lookup and integration
- Web search for additional biographical information
- Temporal pattern analysis using commit dates

### 2. **Affiliation Tracking**
- Current affiliations
- Historical affiliations
- Affiliation changes over time
- Temporal alignment between commits and affiliations

### 3. **Confidence Scoring**
Each enriched author receives a confidence score (0.0 to 1.0) based on:
- Quality of sources (ORCID = high, institutional email = high, web search = moderate)
- Consistency across multiple sources
- Temporal alignment between commits and affiliation periods
- Amount and recency of contributions

## Architecture

The module is structured similarly to `organization_enrichment.py`:

```
user_enrichment.py
├── EnrichedAuthor (Pydantic Model)
│   ├── name
│   ├── email
│   ├── orcidId
│   ├── affiliations
│   ├── currentAffiliation
│   ├── affiliationHistory
│   ├── contributionSummary
│   ├── confidenceScore
│   └── additionalInfo
│
├── UserEnrichmentResult (Pydantic Model)
│   ├── enrichedAuthors: List[EnrichedAuthor]
│   └── summary: str
│
├── UserAnalysisContext (Pydantic Model)
│   ├── repository_url
│   ├── git_authors
│   └── existing_authors
│
└── PydanticAI Agent with Tools:
    ├── search_orcid()
    ├── search_web() (DuckDuckGo)
    └── extract_domain_from_email()
```

## Agent Tools

### `search_orcid(author_name, email)`
Searches the ORCID API for author information.
- Queries by name and/or email
- Returns ORCID IDs and basic profile information
- Note: Full affiliation details require separate API calls

### `search_web(query)`
Searches DuckDuckGo for information about a person.
- Uses Selenium for web scraping
- Includes retry logic (up to 3 attempts)
- Returns top 5 search results with titles, links, and snippets
- Semaphore-limited to prevent overwhelming Selenium server

### `extract_domain_from_email(email)`
Extracts and analyzes email domains.
- Identifies known institutional domains (EPFL, ETH, etc.)
- Provides organization information for known domains
- Suggests searches for unknown domains

## Usage

### Basic Usage

```python
from src.core.user_enrichment import enrich_users
from src.core.models import GitAuthor, Person, Commits
from datetime import date

# Prepare git author data
git_authors = [
    GitAuthor(
        name="John Doe",
        email="john.doe@epfl.ch",
        commits=Commits(
            total=45,
            firstCommitDate=date(2022, 1, 15),
            lastCommitDate=date(2024, 3, 20),
        ),
    ),
]

# Existing author information (e.g., from ORCID)
existing_authors = [
    Person(
        name="John Doe",
        orcidId="https://orcid.org/0000-0001-2345-6789",
        affiliation=["EPFL"],
    ),
]

# Enrich user information
result = await enrich_users(
    git_authors=git_authors,
    existing_authors=existing_authors,
    repository_url="https://github.com/example/repo",
)

# Access enriched data
for author in result.enrichedAuthors:
    print(f"{author.name}: {author.currentAffiliation}")
    print(f"Confidence: {author.confidenceScore}")
```

### Using Dictionary Data

```python
from src.core.user_enrichment import enrich_users_from_dict

# Data from API or other sources
git_authors_data = [
    {
        "name": "Alice Johnson",
        "email": "alice@datascience.ch",
        "commits": {
            "total": 67,
            "firstCommitDate": "2020-09-01",
            "lastCommitDate": "2024-10-01",
        },
    },
]

existing_authors_data = [
    {
        "name": "Alice Johnson",
        "orcidId": "https://orcid.org/0000-0002-3456-7890",
        "affiliation": ["Swiss Data Science Center"],
    },
]

# Enrich and get result as dictionary
result_dict = await enrich_users_from_dict(
    git_authors_data=git_authors_data,
    existing_authors_data=existing_authors_data,
    repository_url="https://github.com/example/repo",
)
```

## Environment Variables

- `MODEL`: OpenAI model to use (default: `gpt-4o-mini`)
- `OPENAI_API_KEY`: Required for OpenAI API access
- `SELENIUM_REMOTE_URL`: Selenium server URL (default: `http://selenium-standalone-firefox:4444`)
- `MAX_SELENIUM_SESSIONS`: Maximum concurrent Selenium sessions (default: `3`)

## Confidence Scoring Guidelines

The module uses the following confidence scoring guidelines:

| Score Range | Evidence Level | Description |
|-------------|---------------|-------------|
| 0.9 - 1.0   | Strong        | ORCID + institutional email + recent activity |
| 0.7 - 0.89  | Good          | ORCID or institutional email + significant commits |
| 0.5 - 0.69  | Moderate      | Partial information + some commits |
| 0.3 - 0.49  | Weak          | Limited information or old/few commits |
| 0.0 - 0.29  | Very weak     | Speculative evidence only |

## Output Structure

The `UserEnrichmentResult` contains:

```python
{
    "enrichedAuthors": [
        {
            "name": "John Doe",
            "email": "john.doe@epfl.ch",
            "orcidId": "https://orcid.org/0000-0001-2345-6789",
            "affiliations": ["EPFL", "Swiss Data Science Center"],
            "currentAffiliation": "EPFL",
            "affiliationHistory": [
                {
                    "organization": "EPFL",
                    "startDate": "2022-01-01",
                    "endDate": None,
                    "source": "email domain + ORCID"
                }
            ],
            "contributionSummary": "45 commits from 2022-01-15 to 2024-03-20",
            "confidenceScore": 0.95,
            "additionalInfo": "Senior researcher at EPFL..."
        }
    ],
    "summary": "Repository has 3 main contributors primarily from EPFL and ETH Zürich..."
}
```

## Integration with Existing Code

This module is designed to work seamlessly with the existing codebase:

1. **Models**: Uses existing `GitAuthor` and `Person` models from `src/core/models.py`
2. **Similar API**: Mirrors the API of `organization_enrichment.py` for consistency
3. **Async/Await**: Fully asynchronous for efficient processing
4. **Logging**: Integrated with the project's logging system
5. **Selenium Integration**: Uses the same Selenium setup as organization enrichment

## Examples

See `examples/example_user_enrichment.py` for comprehensive examples including:
- Basic enrichment with git authors
- Enrichment from dictionary data
- Minimal information enrichment

## Dependencies

- `pydantic-ai`: AI agent framework
- `selenium`: Web scraping for DuckDuckGo
- `httpx`: Async HTTP client for ORCID API
- `pydantic`: Data validation and models

## Logging

The module provides detailed logging at multiple levels:
- `INFO`: High-level operations and results
- `DEBUG`: Detailed execution flow and Selenium operations
- `WARNING`: Retry attempts and non-critical issues
- `ERROR`: Failures and exceptions

Example log output:
```
🚀 Starting user enrichment for https://github.com/example/repo
📊 Input data: 3 git authors, 1 existing author records
🤖 Running PydanticAI agent...
🔍 Agent tool called: extract_domain_from_email('john.doe@epfl.ch')
✓ Domain analysis for 'john.doe@epfl.ch': epfl.ch (known)
🔍 Agent tool called: search_orcid('Jane Smith', 'jane.smith@ethz.ch')
✓ ORCID search for 'Jane Smith' returned 2 results
✅ User enrichment completed for https://github.com/example/repo
👥 Enriched 3 authors
```

## Known Limitations

1. **ORCID API**: Basic search only; full affiliation details require additional API calls
2. **Web Search**: DuckDuckGo results may vary; rate limiting may apply
3. **Domain Recognition**: Only recognizes pre-defined institutional domains
4. **Temporal Data**: Relies on commit dates; actual affiliation dates may differ
5. **Disambiguation**: May struggle with common names without additional context

## Future Enhancements

- Full ORCID employment history integration
- More sophisticated name matching algorithms
- Additional institutional domain database
- Integration with other academic databases (Google Scholar, Semantic Scholar)
- Enhanced temporal affiliation analysis
- Support for organizational hierarchies in affiliations
