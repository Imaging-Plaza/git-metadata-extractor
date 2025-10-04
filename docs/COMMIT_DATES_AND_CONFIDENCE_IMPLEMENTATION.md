# Implementation: Git Commit Dates and Organization Confidence Scoring

## Summary

This document describes the implementation of two key features:
1. **Per-author commit date tracking**: Extract first and last commit dates for each git author
2. **Organization confidence scoring**: Provide confidence scores for organization attributions based on commit patterns and ORCID affiliation dates

## Changes Made

### 1. Models (`src/core/models.py`)

#### Updated `Commits` Model
The `Commits` model now includes first and last commit dates:

```python
class Commits(BaseModel):
    total: int = None
    firstCommitDate: Optional[date] = None
    lastCommitDate: Optional[date] = None
```

#### Updated `Organization` Model
Added confidence scoring field (note: there's a typo `confidenceOfAttriution` that should be fixed later):

```python
class Organization(BaseModel):
    # ... existing fields ...
    confidenceOfAttriution: Optional[float] = None  # Confidence score (0.0 to 1.0)
```

#### Updated Mappings
Added mappings for new fields in `PYDANTIC_TO_ZOD_MAPPING`:

```python
"Organization": {
    # ... existing mappings ...
    "confidenceOfAttriution": "imag:confidenceOfAttribution",
},
"Commits": {
    "total": "imag:totalCommits",
    "firstCommitDate": "imag:firstCommitDate",
    "lastCommitDate": "imag:lastCommitDate",
},
```

### 2. Git Author Extraction (`src/core/genai_model.py`)

#### Updated `extract_git_authors()` Function
The function now:
- Extracts total commit count per author (as before)
- Runs `git log --reverse` to get the first commit date per author
- Runs `git log -1` to get the last (most recent) commit date per author
- Returns `GitAuthor` objects with complete `Commits` objects containing dates

Example output:
```python
GitAuthor(
    name="Robin Franken",
    email="robin.franken@epfl.ch",
    commits=Commits(
        total=53,
        firstCommitDate=date(2022, 6, 1),
        lastCommitDate=date(2024, 9, 30)
    )
)
```

#### Updated Serialization
When adding git authors to JSON data, the `Commits` object is now properly serialized:

```python
"commits": {
    "total": author.commits.total,
    "firstCommitDate": author.commits.firstCommitDate.isoformat() if ... else None,
    "lastCommitDate": author.commits.lastCommitDate.isoformat() if ... else None,
}
```

### 3. Organization Enrichment (`src/core/organization_enrichment.py`)

#### Updated `OrganizationEnrichmentResult` Model
Added confidence score for EPFL relationship:

```python
class OrganizationEnrichmentResult(BaseModel):
    organizations: List[Organization]
    relatedToEPFL: bool
    relatedToEPFLConfidence: float  # NEW: Confidence score (0.0 to 1.0)
    relatedToEPFLJustification: str
```

#### Enhanced Agent System Prompt
The agent now receives instructions to:
- Analyze commit date patterns to assess temporal affiliation
- Consider alignment between commit dates and ORCID affiliation periods
- Provide confidence scores for each organization based on:
  - Strength of evidence (institutional email = high, ORCID = high, generic = low)
  - Number and percentage of commits from affiliated authors
  - Temporal alignment between commit dates and affiliation periods
  - Consistency across multiple sources

Confidence scoring guidelines provided to the agent:
- **0.9-1.0**: Strong evidence (institutional email + significant commits + temporal alignment)
- **0.7-0.89**: Good evidence (institutional email or ORCID + moderate commits)
- **0.5-0.69**: Moderate evidence (ORCID affiliation or some commits with institutional email)
- **0.3-0.49**: Weak evidence (few commits or only indirect indicators)
- **0.0-0.29**: Very weak or speculative evidence

#### Updated Prompts
Both `enrich_organizations()` and `enrich_organizations_from_dict()` now send commit date information:

```python
"commits": {
    "total": a.commits.total if a.commits else 0,
    "firstCommitDate": str(a.commits.firstCommitDate) if a.commits and a.commits.firstCommitDate else None,
    "lastCommitDate": str(a.commits.lastCommitDate) if a.commits and a.commits.lastCommitDate else None
}
```

#### Updated Dictionary Parsing
Enhanced `enrich_organizations_from_dict()` to handle both:
- New format: `commits` as a dict with `total`, `firstCommitDate`, `lastCommitDate`
- Legacy format: `commits` as just a number (for backward compatibility)

### 4. JSON-LD Context (`src/files/json-ld-context.json`)

Added new ontology mappings:
```json
"totalCommits": "imag:totalCommits",
"firstCommitDate": "imag:firstCommitDate",
"lastCommitDate": "imag:lastCommitDate",
"confidenceOfAttribution": "imag:confidenceOfAttribution",
"relatedToEPFLConfidence": "imag:relatedToEPFLConfidence"
```

### 5. Examples (`examples/example_organization_enrichment.py`)

Updated to demonstrate new features:
- Git authors now include commit date information
- Example output shows confidence scores
- Added explanation of new analysis steps

## Usage

### For Repositories

The changes are automatically applied when analyzing repositories. No API changes required.

```python
from src.core.genai_model import llm_request_repo_infos

# This will now include commit dates in gitAuthors
result = await llm_request_repo_infos("https://github.com/user/repo")

# Access commit information:
for author in result["gitAuthors"]:
    print(f"{author['name']}: {author['commits']['total']} commits")
    print(f"  First: {author['commits']['firstCommitDate']}")
    print(f"  Last: {author['commits']['lastCommitDate']}")
```

### For Organization Enrichment

```python
from src.core.organization_enrichment import enrich_organizations_from_dict

enrichment = await enrich_organizations_from_dict(llm_output, repo_url)

# Access confidence scores:
print(f"EPFL Confidence: {enrichment['relatedToEPFLConfidence']}")

for org in enrichment['organizations']:
    print(f"{org['legalName']}: {org.get('confidenceOfAttriution', 'N/A')}")
```

## Benefits

1. **Temporal Analysis**: Can now determine if contributors were active during specific periods
2. **Affiliation Accuracy**: Better matching of ORCID affiliation periods with actual contribution periods
3. **Confidence Metrics**: Quantified confidence in organization attributions
4. **Better EPFL Detection**: More accurate identification of EPFL-related repositories based on commit patterns

## Future Improvements

1. Fix the typo: `confidenceOfAttriution` → `confidenceOfAttribution` (requires schema update)
2. Consider extracting ORCID affiliation start/end dates for even better temporal alignment
3. Add visualization of commit timelines per author
4. Consider weighting commits by recency (recent commits might indicate current affiliation)

## Testing

A test file `test_commit_dates.py` has been created to verify the commit date extraction functionality works correctly.

Run it with:
```bash
python test_commit_dates.py
```

## Backward Compatibility

The implementation maintains backward compatibility:
- Legacy data with `commits` as a number will still work
- Missing commit dates are handled gracefully (set to `None`)
- Existing API endpoints continue to function without changes
