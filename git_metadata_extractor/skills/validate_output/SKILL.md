---
name: validate-output
description: Validate `output.jsonld` against the v2 SHACL shapes graph. Returns whether the graph conforms, plus the full list of violations and warnings (focus node, property path, value, message). Call this BEFORE declaring done; iterate on your own output until conforms=true. Structural issues are YOUR responsibility, not the judge's.
---

# validate-output

Run the v2 SHACL shapes graph against your `output.jsonld` and get back
a structured report of every violation, with enough detail to fix them
yourself.

## When to use this

**Always**, near the end of every run, before you stop:

1. Finish your usual loop (read gimie, call skills, write entities).
2. Call `validate-output`.
3. If `conforms=false`, read each violation, edit `output.jsonld` to
   remove the offending property OR fix its value, and write it back.
4. Call `validate-output` again. Repeat until `conforms=true`.
5. Only then declare done.

A SHACL-violating output costs you the run. Catching it yourself is
free; catching it from the judge's feedback in a repair iteration is
expensive (full executor re-run).

## Command

```
gme-validate-output [--path output.jsonld] [--max-violations N]
python -m git_metadata_extractor.skills.validate_output [--path output.jsonld] [--max-violations N]
```

| Arg | Default | Notes |
|---|---|---|
| `--path` | `output.jsonld` | Relative to current working directory (the run's tempdir, where you already are). |
| `--max-violations` | `50` | Truncate the list. Defaults are usually enough. |

## Output

JSON object on stdout:

```json
{
  "conforms": true,
  "violation_count": 0,
  "warning_count": 0,
  "violations": [],
  "warnings": [],
  "path": "output.jsonld"
}
```

Failure mode (file invalid / SHACL deps missing): structured `{error,
kind}` on stderr, non-zero exit.

When violations exist, each entry has:

```json
{
  "focus": "https://github.com/foo/bar",
  "path": "http://schema.org/contributor",
  "value": null,
  "severity": "http://www.w3.org/ns/shacl#Violation",
  "message": "Node ... is closed. It cannot have value: <https://github.com/alice>"
}
```

## How to read a violation and fix it

| Violation message | What to do |
|---|---|
| `Node X is closed. It cannot have value Y on path P` | Remove property `P` from entity `X`. It is not in the closed shape — see `schema_cheatsheet.md` for the allowed list. |
| `Less than 1 values on X->P` | Required property `P` is missing on entity `X`. Add it (cheatsheet shows `required=yes`). |
| `Value is not Literal with datatype xsd:dateTime` | The value is a date-only string like `"2022-12-07"`. Append `T00:00:00Z` → `"2022-12-07T00:00:00Z"`. |
| `Value does not match pattern '^https://ror\.org/...'` | The value is the raw id (`02hdt9m26`) where the shape requires the full URL (`https://ror.org/02hdt9m26`). |

## Examples

```
gme-validate-output
gme-validate-output --path output.jsonld --max-violations 10
```

## Notes for the executor

- This skill is fast (~5s). Calling it 2-4 times in a run is fine.
- You can also run the same SHACL check on a hand-edited subset by
  pointing `--path` at any JSON-LD file inside your tempdir.
- After every successful `conforms=true` you can stop calling tools and
  end the run.
