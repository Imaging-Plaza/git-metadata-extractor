# GitHub dependents page — HTML selectors

URL: `https://github.com/{owner}/{repo}/network/dependents?dependent_type=REPOSITORY|PACKAGE`

Captured 2026-05-01 via Selenium (Firefox headless). All findings verified
against the three fixtures in this directory.

## Layout overview

The page has:

1. A toggle bar with two `<a>` links — one for `REPOSITORY` view, one for
   `PACKAGE` view. The currently-shown view carries `class="btn-link selected"`;
   the other carries `class="btn-link "` (trailing space significant in the
   live HTML).
2. A list of rows (one `<div data-test-id="dg-repo-pkg-dependent">` per
   dependent).
3. A pagination footer with `Previous` and `Next` buttons.

## Selectors

| What | Selector / pattern |
|---|---|
| Total count + selected view | `a.btn-link.selected` — text reads `"<N> Repositories"` or `"<N> Packages"` |
| Other view's count | `a.btn-link:not(.selected)` — same shape |
| Each dependent row | `[data-test-id="dg-repo-pkg-dependent"]` |
| Owner login | inside row, first `<a>` with `data-hovercard-type` (`organization` or `user`); also available verbatim as the avatar `<img alt="@<owner>">` |
| Repository name | inside row, `<a class="text-bold">`; `href="/<owner>/<repo>"` is the canonical reference |
| Star count | inside row, text node immediately after `svg.octicon-star`. Number is comma-formatted (e.g. `"4,065,575"`). |
| Fork count | inside row, text node immediately after `svg.octicon-repo-forked`. Same format. |
| Next-page link | `<a rel="nofollow" class="btn BtnGroup-item" href="...?dependents_after=<cursor>">Next</a>` |
| End of pagination | `Next` becomes `<button class="btn BtnGroup-item" disabled="disabled">Next</button>` |

## Fixture summary

| File | Repo | Kind | Rows | Total count | Pagination |
|---|---|---|---:|---:|---|
| `sdsc-ordes_gimie_repository.html` | sdsc-ordes/gimie | REPOSITORY | 4 | 6 | end (disabled) |
| `psf_requests_repository.html` | psf/requests | REPOSITORY | 30 | 4,065,575 | next page available |
| `python-poetry_poetry_package.html` | python-poetry/poetry | PACKAGE | 0 | 0 | n/a (empty) |

## Edge cases caught by the fixtures

- **Empty graph** (poetry/PACKAGE): zero rows, `0 Repositories`, `0 Packages`. Pagination buttons still rendered but disabled.
- **End of pagination** (gimie): `Next` is a `<button disabled>`, not an anchor.
- **Cursor format** (psf/requests): query param is `dependents_after=<base64-ish>`. We just preserve the URL verbatim — don't try to decode the cursor.
- **Comma-formatted counts** (psf/requests): "4,065,575" must have commas stripped before `int(...)`.

## Things we deliberately *don't* parse

- Avatar URLs — not useful for downstream consumers.
- Hovercard URLs — internal GitHub UI thing.
- The toggle bar's *non-selected* count is captured for completeness but not part of the primary `DependentsResult`.
