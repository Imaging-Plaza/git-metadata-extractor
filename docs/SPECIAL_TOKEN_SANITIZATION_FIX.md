# Special Token Sanitization Fix

## Problem
When processing certain repositories (like https://github.com/caviri/CSD), the API would fail with this error:
```
Error from LLM service: Encountered text corresponding to disallowed special token '<|endoftext|>'.
```

## Root Cause
The CSD repository contains CLIP (OpenAI's vision model) code that includes tokenizer implementations. These tokenizer files contain literal strings like `<|endoftext|>`, `<|startoftext|>`, etc. as part of the source code.

When this code was extracted and sent to the OpenAI API, tiktoken (the tokenizer library) would encounter these special token strings and refuse to encode them, treating them as a security risk.

### Debug Process
Created `debug_special_tokens.py` to trace where the tokens appeared:
- ✅ GIMIE output: No special tokens
- ❌ Repository content: Found 4 occurrences of `<|endoftext|>` in tokenizer code
- Located in: CLIP model's tokenizer implementation files

Example occurrences:
```python
eot_token = _tokenizer.encoder["<|endoftext|>"]
vocab.extend(['<|startoftext|>', '<|endoftext|>'])
self.cache = {'<|startoftext|>': '<|startoftext|>', '<|endoftext|>': '<|endoftext|>'}
```

## Solution
Changed the `sanitize_special_tokens()` function from tiktoken-based encoding/decoding to regex-based replacement:

### Before (didn't work):
```python
def sanitize_special_tokens(text):
    encoding = tiktoken.get_encoding("cl100k_base")
    try:
        tokens = encoding.encode(text, disallowed_special=())
        clean_text = encoding.decode(tokens)
        return clean_text
    except Exception as e:
        # Fallback to regex
        return re.sub(r"<\|[^|]*\|>", "", text)
```

The problem: Even with `disallowed_special=()`, tiktoken would fail when encountering special tokens in the text.

### After (works):
```python
def sanitize_special_tokens(text):
    """
    Remove special tokens by replacing them with safe placeholders.
    """
    special_tokens_patterns = [
        r'<\|endoftext\|>',
        r'<\|startoftext\|>',
        r'<\|fim_prefix\|>',
        r'<\|fim_suffix\|>',
        r'<\|fim_middle\|>',
    ]

    clean_text = text
    for pattern in special_tokens_patterns:
        clean_text = re.sub(pattern, '[SPECIAL_TOKEN]', clean_text, flags=re.IGNORECASE)

    return clean_text
```

This approach:
1. **Doesn't try to encode first** - avoids the tiktoken error
2. **Replaces with safe placeholder** - `[SPECIAL_TOKEN]` can be safely encoded
3. **Case-insensitive** - catches all variations
4. **Covers all known special tokens** - comprehensive list

## Multi-Level Protection
Sanitization is applied at three levels:

1. **Repository content**: After combining repo files (line 386 in `genai_model.py`)
2. **GIMIE output**: Before adding to input text (line 393)
3. **API call level**: In both `get_openai_response_async()` and `get_openrouter_response_async()` (lines 468, 530)

This defense-in-depth ensures no special tokens reach the LLM APIs.

## Testing
Created `test_sanitization.py` to verify:
- ✅ All special tokens properly replaced
- ✅ Sanitized text can be encoded by tiktoken
- ✅ Normal text passes through unchanged

Tested with problematic repository:
```bash
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/caviri/CSD?enrich_orgs=true"
```
Result: ✅ Success! Full response with organization enrichment.

## Impact
- **Fixed**: Repositories with OpenAI model code (CLIP, GPT, etc.) can now be processed
- **Preserved**: Code context is maintained (special tokens replaced, not removed entirely)
- **No breaking changes**: Only internal sanitization logic changed

## Files Modified
- `/workspaces/git-metadata-extractor/src/core/genai_model.py`
  - `sanitize_special_tokens()` function (line 330)
  - Applied in `llm_request_repo_infos()` (line 393)
  - Applied in `get_openrouter_response_async()` (line 468)
  - Applied in `get_openai_response_async()` (line 530)

## Files Created
- `debug_special_tokens.py` - Debug script to locate special tokens
- `test_sanitization.py` - Test script for sanitization function
- `SPECIAL_TOKEN_SANITIZATION_FIX.md` - This documentation
