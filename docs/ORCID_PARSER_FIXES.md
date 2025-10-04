# ORCID Parser Fixes

## Problem
The ORCID parser was not extracting dates, roles, degrees, locations, or durations from ORCID profiles.

## Root Cause
The original parser was trying to find HTML containers with specific classes, but the ORCID page structure doesn't use those classes consistently. Additionally, the parser was searching within individual containers, but the data was split across multiple DOM elements.

## Solution
Completely rewrote the extraction logic to parse the text content line-by-line instead of relying on HTML structure.

## Changes Made

### 1. `_extract_employment_from_orcid_selenium()` (lines ~354-454)
**Before**: Tried to find containers with classes matching "affiliation|employment"
**After**:
- Extract all text from the employment section
- Parse line-by-line, looking for organization names (contain ":")
- Collect location parts (2-letter country codes, city names)
- Detect date ranges (e.g., "2021 to 2025" or "2018-10-01 to 2021-12-31")
- Extract roles marked with "|" separator
- Build complete employment entries with all fields

### 2. `_extract_education_from_orcid_selenium()` (lines ~457-557)
**Same approach as employment**:
- Line-by-line text parsing
- Organization names with ":"
- Date range detection
- Degree extraction with "|" separator
- Location parsing

### 3. `_extract_dates_from_text()` (lines ~550-600)
**Enhanced** to handle multiple date formats:
- `YYYY-MM-DD to YYYY-MM-DD` (full dates)
- `YYYY to YYYY` (years only)
- `YYYY-MM-DD to present` (ongoing)
- Falls back to finding individual dates

### 4. `_extract_role_from_text()` (lines ~597-627)
**Enhanced** to recognize ORCID's "|" separator format:
- First tries to extract text after "|" (ORCID format)
- Filters out dates and locations
- Falls back to keyword-based extraction

### 5. `_extract_degree_from_text()` (lines ~630-652)
**Enhanced** to recognize "|" separator for degrees:
- Extracts text after "|" if it contains degree keywords
- Supports common degree formats (PhD, MSc, BSc, etc.)

### 6. `_calculate_duration()` (lines ~851-891)
**Enhanced** to handle full date formats:
- Now supports both `YYYY` and `YYYY-MM-DD` formats
- Calculates precise duration in years with decimals
- Example: 2018-10-01 to 2021-12-31 = 3.2 years

### 7. Location Parsing
**Fixed** to remove extra commas:
- Filters out empty strings
- Strips trailing commas from location parts
- Joins with ", " separator cleanly

## Test Results

Testing with ORCID: 0000-0002-1126-1535 (Cyril Matthey-Doret)

### Employment ✅
- ✅ Institut Pasteur (2018-10-01 to 2021-12-31) - PhD Student - 3.2 years
- ✅ Université de Lausanne (2018-03-01 to 2018-08-01) - Bioinformatician - 0.4 years
- ✅ EPFL (2021 to 2025) - 4.0 years

### Education ✅
- ✅ MSc in Bioinformatics (2016-09-11 to 2018-02-14) - 1.4 years
- ✅ BSc in Biology (2013-09-18 to 2016-07-17) - 2.8 years

### All Fields Extracted:
- ✅ **Dates**: Both start and end, supporting YYYY and YYYY-MM-DD formats
- ✅ **Roles**: PhD Student, Bioinformatician
- ✅ **Degrees**: MSc in Bioinformatics, BSc in Biology
- ✅ **Locations**: Paris, Île-de-France, FR / Lausanne, Vaud, CH
- ✅ **Durations**: Precise calculations with decimal years (e.g., 3.2 years)

## Impact on Organization Enrichment

With these fixes, the organization enrichment agent can now:
1. **Use precise affiliation dates** from ORCID to align with commit dates
2. **Calculate confidence scores** based on temporal overlap between affiliations and contributions
3. **Better identify organization types** (PhD Student at Institut Pasteur = research institute)
4. **More accurately attribute organizations** using verified ORCID data

## Files Modified
- `/workspaces/git-metadata-extractor/src/core/users_parser.py`

## Testing
Run the test with:
```bash
python test_orcid_parsing.py
```
