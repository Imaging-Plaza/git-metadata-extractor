---
name: selenium-fetch
description: Render an http(s) URL in headless Firefox via Selenium Grid and return its extracted text excerpt + final URL + page title. Use to verify a link the agent emitted, fetch a project's homepage, or pull text from a JS-heavy page that plain HTTP can't render.
---

# selenium-fetch

Render an http(s) URL in headless Firefox via Selenium Grid and return
the extracted text excerpt and metadata.

## When to use this

Use when:
- You need to verify a link the agent is about to put in the JSON-LD
  output (does it 200? does it really describe the repo?).
- You need text from a JS-heavy page (project homepage, lab landing
  page, ROR detail page) that plain `curl`/`wget` can't render.

Do NOT use for:
- Searching identifier registries (use the `search-*` skills instead).
- Bulk crawling — single-shot only, no rate limiting.

## Requirements

- `SELENIUM_REMOTE_URL` env var must point at a running Selenium Grid
  hub. If unset the skill fails with `provider_unavailable`.

## Command

```
gme-selenium-fetch "<url>" [--max-chars N]
python -m src.v2.skills.selenium_fetch "<url>" [--max-chars N]
```

| Arg | Default | Notes |
|---|---|---|
| `url` (positional) | required | http(s) URL. Other schemes rejected. |
| `--max-chars` | `4000` | Truncate body text. Hard cap: `20000`. |

## Output

JSON object on stdout:

```json
{
  "url": "<input url>",
  "fetched": true,
  "final_url": "<after redirects>",
  "title": "<page title>",
  "text_excerpt": "<first --max-chars of body text>",
  "content_length": 12345,
  "error": null
}
```

When the page is unreachable or invalid: `fetched: false`, `error`
populated, exit code 3 (still a JSON success on stdout — the failure
is described in the payload, not in stderr).

When `SELENIUM_REMOTE_URL` is unset: stderr error JSON, exit 1.

## Examples

```
gme-selenium-fetch "https://imaging-plaza.epfl.ch/"
gme-selenium-fetch "https://ror.org/02s376052" --max-chars 8000
gme-selenium-fetch "https://github.com/SDSC-ORD/gimie"
```

## Notes for the executor

- Use sparingly. Each fetch costs ~5–15 seconds.
- The `text_excerpt` is plain text after BeautifulSoup; JS-rendered
  content will be present (Selenium runs the page) but tag structure is
  lost. Good for "is this URL really about X?" checks.
- `final_url` differs from `url` when there's a redirect chain — useful
  to confirm canonical URLs.
