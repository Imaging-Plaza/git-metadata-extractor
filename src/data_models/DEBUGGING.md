# Pydantic Validation Debugging Guide

This guide explains how to use the comprehensive debugging utilities added to the data models.

## Overview

The debugging utilities provide detailed logging and error reporting for Pydantic validation issues, helping you identify and fix data model problems.

## Available Functions

### 1. `debug_pydantic_validation(data, model_class, context="")`

Comprehensive validation debugging that:
- Logs input data structure
- Attempts validation with detailed error reporting
- Shows field-by-field validation errors

**Usage:**
```python
from data_models import debug_pydantic_validation, SoftwareSourceCode

try:
    validated_data = debug_pydantic_validation(
        data, 
        SoftwareSourceCode, 
        "for repository https://github.com/user/repo"
    )
except ValidationError as e:
    # Detailed errors are already logged
    pass
```

### 2. `log_validation_errors(validation_error, context="")`

Logs detailed information about ValidationError objects.

**Usage:**
```python
from data_models import log_validation_errors
from pydantic import ValidationError

try:
    validated_data = SoftwareSourceCode.model_validate(data)
except ValidationError as e:
    log_validation_errors(e, "during repository analysis")
```

### 3. `debug_field_values(data, model_class)`

Logs field values before validation to help understand what data is being processed.

**Usage:**
```python
from data_models import debug_field_values, SoftwareSourceCode

debug_field_values(data, SoftwareSourceCode)
```

### 4. `validate_repository_data_with_debugging(data, repo_url="")`

Convenience function that combines all debugging utilities.

**Usage:**
```python
from data_models import validate_repository_data_with_debugging

try:
    validated_data = validate_repository_data_with_debugging(
        data, 
        "https://github.com/user/repo"
    )
except ValidationError:
    # All debugging information is logged
    pass
```

## Built-in Field Validators

The models now include built-in validators with logging:

### SoftwareSourceCode Model
- `author` field: Logs author count and missing names
- `gitAuthors` field: Logs git author information
- `hasParameter` field: Logs parameter details including hasDimensionality

### FormalParameter Model
- `hasDimensionality` field: Logs validation of positive integer constraints

### GitAuthor Model
- `commits` field: Logs commits object validation

## Example Output

When validation fails, you'll see detailed logs like:

```
ERROR: Validation failed with 3 errors:
ERROR: Error 1:
ERROR:   Field: hasParameter -> 0 -> hasDimensionality
ERROR:   Type: int_type
ERROR:   Message: Input should be a valid integer
ERROR:   Input: None
ERROR: Error 2:
ERROR:   Field: author -> 0 -> name
ERROR:   Type: missing
ERROR:   Message: Field required
ERROR:   Input: {'orcidId': 'https://orcid.org/0000-0002-1126-1535'}
```

## Integration with Existing Code

To integrate debugging into your existing validation code:

```python
# Before
validated_data = SoftwareSourceCode.model_validate(data)

# After
from data_models import debug_pydantic_validation
validated_data = debug_pydantic_validation(data, SoftwareSourceCode, f"for {repo_url}")
```

## Logging Configuration

Make sure your logging is configured to show DEBUG level messages:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Common Issues Fixed

1. **hasDimensionality validation errors**: Now properly handles None values
2. **Missing author names**: Logs when authors don't have required name fields
3. **Author list removal**: Better handling of empty author lists
4. **GitAuthor commits field**: Proper validation of commits objects

## Troubleshooting

If you're still seeing validation errors:

1. Check the detailed error logs for specific field issues
2. Use `debug_field_values()` to see what data is being validated
3. Look for field validators that might be rejecting valid data
4. Check that required fields have proper defaults or are marked as Optional

## Performance Note

The debugging utilities add some overhead due to logging. In production, you may want to:
- Use them only when validation fails
- Set logging level to WARNING or ERROR to reduce debug output
- Remove or disable validators for performance-critical code paths
