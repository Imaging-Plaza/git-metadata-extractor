# Landing page and documentation publishing

The visual field guide is the site's landing page. Reference documentation
lives under `/docs/` on the same GitHub Pages site:

| URL, relative to the project site | Content |
|---|---|
| `/` | Visual introduction, C4 diagrams and interactive sequences |
| `/docs/` | Documentation entry point |
| `/docs/latest/` | Reference docs built from `main` |
| `/docs/stable/` | Most recently published release reference |
| `/docs/<version>/` | A preserved reference version |

The project site root is
`https://imaging-plaza.github.io/git-metadata-extractor/`. The repository prefix
is part of every URL; `/docs/` here does not mean the organization's domain root.
The `stable` alias exists only after a release has been published.

## Preview the complete site

Install the `docs` extra in your project environment, then run:

```bash
just docs-build
just docs-serve
```

Open `http://127.0.0.1:8000/` for the landing page and `/docs/` for the reference
documentation. This preview is a single current build, so it has no release
selector history. Rerun the recipe after edits. `mkdocs serve` remains useful
for working on reference pages alone, with MkDocs live reload.

`scripts/docs/build_site.py` invokes the strict MkDocs build and assembles the
site. Source Markdown stays in `docs/`; the build changes public routes without
moving the source tree. The visual guide is authored once in `docs/guide/`.
Its documentation links and assets are adjusted for the landing page during
assembly. Existing reference links to `guide/index.html` redirect back to the
landing page, preserving section fragments.

## GitHub Pages configuration

Use **Settings → Pages → Build and deployment → Source: GitHub Actions**.
The workflow is `.github/workflows/docs_pages.yml`. It uses GitHub's
[artifact publishing workflow](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
including the `github-pages` environment and `pages: write` / `id-token: write`
deployment permissions.

The `github-pages` environment must allow the `main` branch and `v*` tags.
Keep these deployment rules aligned with the workflow's permitted refs.

The old setting, `gh-pages` branch with `/docs` as its source folder, served
source Markdown instead of the versioned build. The new workflow uploads only
the assembled `site/` artifact. It does not publish the repository checkout.

PR CI tests the assembler and builds the complete site. Publishing runs on a
push to `main`, a `v*` tag, or a manual run on one of those refs. PR branches
cannot deploy through this workflow. PR #171 targets `develop`; merging there
alone does not publish the site. The change must reach `main` or a release tag.
Tags that predate this workflow retain their older workflow definition; the new
publisher applies to tags containing this configuration.

Main updates `dev` and `latest`. Tags publish their version and update `stable`.
Tagged builds use the landing-page source from `main`, so publishing an older
reference release does not roll back the introduction. Concurrent publications
are serialized to protect the version archive.

## Versions and manual publication

[Mike](https://github.com/jimporter/mike) keeps the reference versions in the
`gh-pages` Git branch. The assembler exports only versions listed in
`versions.json`, puts them under `site/docs/`, and materializes aliases as real
directories. Unrelated files already on `gh-pages` are excluded. This also
avoids symbolic links in the Pages artifact.

The `/docs/` entry point chooses `latest`, then `stable`, then the first
available version. The old `docs-set-default` recipe is retired: setting a
redirect at the archive branch root does not control the artifact's landing
page. Use explicit version links when a release must be pinned.

```bash
just docs-version-list
just docs-deploy-dev                 # dispatch on main
just docs-deploy-release 3.1.0        # dispatch on the existing v3.1.0 tag
```

Publication recipes require an authenticated `gh` CLI and permission to run
the workflow. They dispatch GitHub Actions rather than directly pushing a
partial site. The workflow validates the build before persisting new versions
and uploading the Pages artifact. No deployment occurs during a local build.
