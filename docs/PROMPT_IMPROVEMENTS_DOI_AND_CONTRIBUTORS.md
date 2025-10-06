# Prompt Improvements for DOI Validation and README Contributors

## Changes Made

Updated the main system prompt in `/src/core/prompts.py` to address two critical issues:

### 1. Invalid DOI Prevention

**Problem**: The LLM was generating invalid placeholder DOIs like `https://doi.org/10.0000/unknown` and including Zenodo links in the wrong fields.

**Solution**: Added explicit warnings and instructions:

#### In Objectives Section:
```
⚠️ **CRITICAL - DOI and Citation Rules:**
- **NEVER use placeholder DOIs** like `https://doi.org/10.0000/unknown` or any DOI with `10.0000/` - these are invalid.
- **DO NOT include Zenodo links in the `identifier` field** - Zenodo links should go in `relatedDatasets` instead.
- **If no valid DOI or identifier exists, leave the `identifier` field as an empty string `""`** - it's better to have it blank than invalid.
- For `citation` field: Only include **valid URLs to actual published papers** (DOI, arXiv, journal URLs).
- If no citations are found, leave `citation` as an empty array `[]` - do not make up placeholder citations.
```

#### Updated Field Descriptions:

**`identifier` field:**
```
- `identifier` (string, **required but can be empty**): Unique identifier such as a **valid DOI** (e.g., `https://doi.org/10.1234/actual-doi`).
  - **Leave as empty string `""` if no valid identifier exists**.
  - **DO NOT use placeholder DOIs** like `10.0000/unknown`.
  - **DO NOT use Zenodo links here** - those belong in `relatedDatasets`.
```

**`citation` field:**
```
- `citation` (list of **valid URLs**, **required but can be empty**): Academic references or citations. These should be URLs to **actual published scientific articles**, arXiv papers, or **valid DOI links**.
  - **Leave as empty array `[]` if no valid citations exist**.
  - **DO NOT include placeholder DOIs** or invalid references.
  - **DO NOT include Zenodo dataset links here** - those belong in `relatedDatasets`.
```

#### Final Reminders Section:
```
**IMPORTANT REMINDERS:**
1. Check README for contributors/authors sections - add all mentioned people to the author list.
2. NEVER use placeholder DOIs like `https://doi.org/10.0000/unknown` - leave identifier empty if none exists.
3. DO NOT put Zenodo links in `identifier` or `citation` - they belong in `relatedDatasets`.
4. If no valid citations exist, leave `citation` as an empty array `[]`.
5. Only include real, verifiable citations and identifiers.
```

### 2. README Contributors Extraction

**Problem**: Contributors mentioned in README files were not being extracted and added to the author list.

**Solution**: Added explicit instructions to check README for contributors:

#### In Objectives Section:
```
4. **Check the README for contributors section** - any people mentioned as contributors, maintainers, or team members should be added to the author list with their information.
```

#### In Author Field Description:
```
- `author`: Each author must be an object containing:
  - `name`
  - `orcidId`
  - `affiliation` (list of strings, **optional**)
  - **IMPORTANT**: Check the README file for any "Contributors", "Authors", "Team", "Maintainers", or "Acknowledgments" sections and add those people to the author list.
  - Look for GitHub usernames, email addresses, or names mentioned in these sections.
```

## Expected Behavior Changes

### Before:

**Invalid DOI generation:**
```json
{
  "identifier": "https://doi.org/10.0000/unknown",
  "citation": ["https://zenodo.org/record/12345"]
}
```

**Missing contributors:**
- People mentioned in README Contributors section were ignored
- Only Git commit authors were included

### After:

**Proper handling of missing identifiers:**
```json
{
  "identifier": "",  // Empty if no valid DOI found
  "citation": [],    // Empty if no valid citations found
  "relatedDatasets": ["https://zenodo.org/record/12345"]  // Zenodo moved here
}
```

**Complete author list:**
```json
{
  "author": [
    {"name": "Git Commit Author", "orcidId": "..."},
    {"name": "README Contributor 1", "orcidId": null},
    {"name": "README Maintainer", "orcidId": null}
  ]
}
```

## Validation Impact

These changes will:

1. ✅ **Eliminate invalid DOI errors** - No more `https://doi.org/10.0000/unknown`
2. ✅ **Proper field usage** - Zenodo links go to `relatedDatasets`, not `identifier` or `citation`
3. ✅ **More complete author lists** - Contributors from README are now included
4. ✅ **Better data quality** - Empty fields instead of placeholder/invalid data

## Testing Recommendations

After deploying these changes, verify:

1. Check that `identifier` fields are either:
   - Valid DOIs (e.g., `https://doi.org/10.1038/...`)
   - Empty strings `""`
   - Never `https://doi.org/10.0000/unknown`

2. Check that Zenodo links appear in:
   - ✅ `relatedDatasets` field
   - ❌ NOT in `identifier` field
   - ❌ NOT in `citation` field

3. Check that `author` lists include:
   - Git commit authors
   - People from README Contributors/Authors/Team sections
   - Maintainers mentioned in documentation

## Related Fields

The prompt also clarifies where different types of links should go:

- **Valid DOIs for the software itself** → `identifier`
- **Published paper DOIs/URLs** → `citation`
- **Zenodo dataset links** → `relatedDatasets`
- **HuggingFace datasets** → `relatedDatasets`
- **ArXiv papers** → `citation` (if citing the software) or `relatedPublications`
- **Models (HuggingFace, etc.)** → `relatedModels`
- **APIs** → `relatedAPI`

## Files Modified

- ✅ `/src/core/prompts.py` - Updated `system_prompt_json` with:
  - DOI validation warnings in objectives
  - README contributor instructions
  - Updated `identifier` field description
  - Updated `citation` field description
  - Updated `author` field description
  - Added final reminders section
