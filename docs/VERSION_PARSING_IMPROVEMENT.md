# Version Parsing Improvement

## Problem

The previous version validation was too strict and would reject valid version strings that had prefixes or different formats:

- ❌ `"Version 0.1.1"` - rejected (has "Version " prefix)
- ❌ `"v1.2.3"` - rejected (has "v" prefix)
- ❌ `"release-2.0.0"` - rejected (has "release-" prefix)

The old regex used `fullmatch(r"\d+\.\d+\.\d+")` which required the ENTIRE string to be exactly `X.Y.Z` with nothing else.

## Solution

Implemented a smarter version parser that:

1. **Validates** - Accepts semantic versions with various prefixes
2. **Normalizes** - Extracts and standardizes the version to `X.Y.Z` format

### New Methods

```python
def _is_version(self, version):
    """
    Validate version string - accepts various formats.
    Returns True if a valid semantic version can be extracted.
    """
    match = re.search(r'v?(\d+)\.(\d+)\.(\d+)', version.lower())
    return bool(match)

def _normalize_version(self, version):
    """
    Extract and normalize semantic version from string.
    Returns normalized version (e.g., "1.2.3") or None.
    """
    match = re.search(r'v?(\d+)\.(\d+)\.(\d+)', version.lower())
    if match:
        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    return None
```

## Supported Formats

### ✅ Now Accepted

| Input Format | Normalized Output | Notes |
|--------------|-------------------|-------|
| `1.2.3` | `1.2.3` | Standard semantic version |
| `0.1.1` | `0.1.1` | Standard format |
| `v1.2.3` | `1.2.3` | Git tag format |
| `V2.0.0` | `2.0.0` | Uppercase V prefix |
| `Version 0.1.1` | `0.1.1` | With "Version" prefix |
| `version 1.2.3` | `1.2.3` | Lowercase "version" |
| `release-1.2.3` | `1.2.3` | Release tag format |
| `tag-0.5.0` | `0.5.0` | Tag format |
| `build 3.4.5` | `3.4.5` | Build format |
| `v1.0.0-beta` | `1.0.0` | Pre-release (extracts base) |
| `2.1.0+build.123` | `2.1.0` | Build metadata (extracts base) |
| `3.2.1-rc1` | `3.2.1` | Release candidate (extracts base) |
| `1.2.3.4` | `1.2.3` | Extracts first X.Y.Z |

### ❌ Still Rejected

| Input | Reason |
|-------|--------|
| `1.2` | Not semantic version (needs X.Y.Z) |
| `1` | Not semantic version |
| `abc` | No version numbers |
| `""` | Empty string |
| `None` | Null value |

## Behavior Changes

### During Validation (`_check_software_images`)

When a software image has a version:

1. **Valid but non-standard format** (e.g., "Version 0.1.1"):
   - ✅ Validation passes
   - 🔄 Version is normalized to "0.1.1"
   - ⚠️ Warning logged about normalization

2. **Invalid format** (e.g., "invalid-version"):
   - ❌ Validation fails
   - ⚠️ Error logged
   - 📋 Added to issues list

### During Sanitization (`sanitize`)

When cleaning up data:

1. **Valid versions**: Normalized to standard format
2. **Invalid versions**: Removed from the data

## Testing

All 21 test cases pass:

```bash
python test_version_parsing.py
```

### Test Results

```
Testing Version Validation and Normalization
============================================================
✅ Input: 1.2.3                          | Valid: True  | Normalized: 1.2.3
✅ Input: Version 0.1.1                  | Valid: True  | Normalized: 0.1.1
✅ Input: v1.2.3                         | Valid: True  | Normalized: 1.2.3
✅ Input: invalid-version                | Valid: False | Normalized: None
... (21 tests total)

Results: 21 passed, 0 failed
✅ All tests passed!
```

## Example Usage

### Before

```json
{
  "hasSoftwareImage": [
    {
      "name": "my-app",
      "softwareVersion": "Version 0.1.1"
    }
  ]
}
```

**Result**: ❌ Error: "Invalid softwareVersion: Version 0.1.1"

### After

```json
{
  "hasSoftwareImage": [
    {
      "name": "my-app",
      "softwareVersion": "0.1.1"  // ✅ Automatically normalized
    }
  ]
}
```

**Result**: ✅ Passes validation with warning about normalization

## Files Modified

1. **`src/core/verification.py`**:
   - Updated `_is_version()` method to use regex search instead of fullmatch
   - Added new `_normalize_version()` method
   - Updated `_check_software_images()` to normalize versions during validation
   - Updated `sanitize()` to normalize versions during cleanup

2. **`test_version_parsing.py`** (new):
   - Comprehensive test suite for version validation
   - Tests normalization behavior
   - Validates software image processing

## Benefits

1. **More user-friendly**: Accepts common version formats from various sources
2. **Data quality**: Automatically standardizes versions to consistent format
3. **Backward compatible**: Still validates semantic versioning (X.Y.Z)
4. **Flexible**: Handles versions from Git tags, Docker tags, release notes, etc.
5. **Informative**: Logs warnings when normalization occurs for transparency

## Related Issues

This fixes the error:
```
❌ Invalid softwareVersion: Version 0.1.1
```

The version is now properly extracted and normalized to `0.1.1`.
