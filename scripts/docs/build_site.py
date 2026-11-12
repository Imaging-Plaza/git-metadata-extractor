#!/usr/bin/env python3
"""Assemble the visual landing page and reference documentation for Pages.

Local: python scripts/docs/build_site.py --output site
Version archive: add --versions-branch gh-pages
Tag releases: add --landing-source /path/to/main-checkout

Only declared Mike versions are exported. Aliases are real directory copies,
and the source guide remains usable independently of this generated artifact.
"""

# CLI validation errors deliberately include the offending path/value.
# ruff: noqa: TRY003, EM101, EM102

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

BUILD_MARKER = ".pages-build"


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _validate_output(repo: Path, source: Path, output: Path) -> Path:
    # Check the lexical path too: resolving it first would conceal a link.
    if any(_is_link(path) for path in (output, *output.parents)):
        raise ValueError(f"Output cannot be a symlink or traverse one: {output}")
    output = output.resolve()
    for root in {repo, source}:
        if output == root or output in root.parents:
            raise ValueError(
                f"Output would replace a source checkout or ancestor: {output}",
            )
        # Protect existing source trees without needing Git in local mode.
        for child in root.iterdir():
            if (
                child.is_dir()
                and child.name != "site"
                and not (child / BUILD_MARKER).is_file()
                and (output == child.resolve() or child.resolve() in output.parents)
            ):
                raise ValueError(f"Output is inside a source directory: {output}")
    if output.exists():
        if not output.is_dir():
            raise ValueError(f"Output is not a directory: {output}")
        previous_mkdocs = (
            output == repo / "site"
            and (output / "index.html").is_file()
            and (output / "search/search_index.json").is_file()
        )
        if (
            any(output.iterdir())
            and not (output / BUILD_MARKER).is_file()
            and not previous_mkdocs
        ):
            raise ValueError(
                f"Refusing to replace an unrecognized nonempty output directory: {output}",
            )
    return output


def _version_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)
        or value.endswith(".")
        or value.lower() in {"versions.json", "index.html", "con", "prn", "aux", "nul"}
        or re.fullmatch(r"(?:com|lpt)[1-9](?:\..*)?", value, re.IGNORECASE)
    ):
        raise ValueError(f"Unsafe version or alias name: {value!r}")
    return value


def _read_versions(repo: Path, branch: str) -> tuple[str, list[dict], str]:
    def git(*args: str) -> str:
        result = subprocess.run(  # noqa: S603 -- argument list, validated ref
            ["git", "-C", str(repo), *args],  # noqa: S607 -- fixed Git executable
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip()

    commit = git("rev-parse", "--verify", "--end-of-options", f"{branch}^{{commit}}")
    versions = json.loads(git("show", f"{commit}:versions.json"))
    if not isinstance(versions, list) or not versions:
        raise ValueError(
            "The version archive must declare at least one version in versions.json",
        )
    names = set()
    for item in versions:
        if not isinstance(item, dict) or not isinstance(item.get("aliases", []), list):
            raise ValueError("Each version must be an object with an aliases list")  # noqa: TRY004 -- malformed JSON is a CLI validation error
        for name in [item.get("version"), *item.get("aliases", [])]:
            _version_name(name)
            if name.lower() in names:
                raise ValueError(f"Duplicate or colliding version/alias: {name}")
            names.add(name.lower())
    all_names = {
        name
        for item in versions
        for name in [item["version"], *item.get("aliases", [])]
    }
    preferred = next(
        (name for name in ("latest", "stable") if name in all_names),
        versions[0]["version"],
    )
    return commit, versions, preferred


def extract_archive(archive: tarfile.TarFile, destination: Path) -> None:
    """Extract regular files/directories only, after validating every member."""
    members = archive.getmembers()
    names = set()
    for member in members:
        parts = PurePosixPath(member.name).parts
        if (
            not parts
            or member.name.startswith("/")
            or "\\" in member.name
            or ":" in member.name
            or any(part in {".", ".."} for part in member.name.split("/"))
            or not (member.isfile() or member.isdir())
        ):
            raise ValueError(f"Unsafe archive entry: {member.name}")
        key = member.name.rstrip("/").lower()
        if key in names:
            raise ValueError(f"Colliding archive entry: {member.name}")
        names.add(key)
        target = destination.joinpath(*parts)
        if destination.resolve() not in target.resolve().parents:
            raise ValueError(f"Archive entry escapes destination: {member.name}")
    for member in members:
        target = destination.joinpath(*PurePosixPath(member.name).parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as incoming, target.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)


def _redirect(destination: str) -> str:
    # Relative destinations preserve project-site prefixes. JS carries the old
    # guide's fragment/query; the link and refresh also work without JavaScript.
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Continue to documentation</title>
<script>location.replace({json.dumps(destination)} + location.search + location.hash);</script>
<meta http-equiv="refresh" content="0; url={escape(destination, quote=True)}">
</head><body><a href="{escape(destination, quote=True)}">Continue</a></body></html>
"""


def _guide_redirect(docs: Path, destination: str) -> None:
    guide = docs / "guide"
    guide.mkdir(parents=True, exist_ok=True)
    (guide / "index.html").write_text(_redirect(destination), encoding="utf-8")


def _export_versions(
    repo: Path, docs: Path, commit: str, versions: list[dict], preferred: str,
) -> None:
    with tempfile.TemporaryFile() as stream:
        subprocess.run(  # noqa: S603 -- validated commit and version names, no shell
            [  # noqa: S607 -- fixed Git executable
                "git",
                "-C",
                str(repo),
                "archive",
                "--format=tar",
                commit,
                "--",
                *[item["version"] for item in versions],
            ],
            check=True,
            stdout=stream,
            stderr=subprocess.PIPE,
        )
        stream.seek(0)
        with tarfile.open(fileobj=stream) as archive:
            extract_archive(archive, docs)
    for item in versions:
        version_dir = docs / item["version"]
        if not (version_dir / "index.html").is_file():
            raise ValueError(f"Declared version has no index.html: {item['version']}")
        _guide_redirect(version_dir, "../../../")
        for alias in item.get("aliases", []):
            shutil.copytree(version_dir, docs / alias)
    (docs / "versions.json").write_text(
        json.dumps(versions, indent=2) + "\n", encoding="utf-8",
    )
    (docs / "index.html").write_text(_redirect(f"{preferred}/"), encoding="utf-8")


def _documentation_url(value: str, prefix: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme
        or url.netloc
        or url.path.startswith("/")
        or ".." in PurePosixPath(url.path).parts
    ):
        raise ValueError(f"Expected a relative documentation source path: {value}")
    if not url.path.endswith(".md"):
        raise ValueError(f"Expected a Markdown documentation source path: {value}")
    path = url.path[:-3]
    if path == "index":
        path = ""
    elif path.endswith("/index"):
        path = path[:-5]
    else:
        path += "/"
    return urlunsplit(("", "", prefix + path, url.query, url.fragment))


class _LandingLinks(HTMLParser):
    """Record minimal start-tag edits, preserving SVG casing and all other text."""

    def __init__(self, source: str, prefix: str):
        super().__init__(convert_charrefs=False)
        self.prefix = prefix
        self.edits = []
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", source))
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        original = self.get_starttag_text()
        updated = original
        if tag == "html":
            updated = original[:-1] + ' data-built-landing="true">'
        else:
            replacement = None
            if tag == "a" and attrs.get("data-doc"):
                replacement = (
                    "href",
                    _documentation_url(attrs["data-doc"], self.prefix),
                )
            elif tag == "link" and attrs.get("href") == "guide.css":
                replacement = ("href", "assets/guide/guide.css")
            elif tag == "script" and attrs.get("src") == "guide.js":
                replacement = ("src", "assets/guide/guide.js")
            if replacement:
                name, value = replacement
                pattern = rf"""\b{name}\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)"""
                updated, count = re.subn(
                    pattern,
                    f'{name}="{escape(value, quote=True)}"',
                    original,
                    count=1,
                    flags=re.IGNORECASE,
                )
                if not count:
                    raise ValueError(f"Missing {name} attribute on guide link")
        if updated != original:
            line, column = self.getpos()
            self.edits.append((self.lines[line - 1] + column, len(original), updated))

    handle_startendtag = handle_starttag


def _landing(source: Path, output: Path, prefix: str) -> None:
    guide = source / "docs/guide"
    html = (guide / "index.html").read_text(encoding="utf-8")
    parser = _LandingLinks(html, prefix)
    for start, length, replacement in reversed(parser.edits):
        html = html[:start] + replacement + html[start + length :]
    (output / "index.html").write_text(html, encoding="utf-8")
    assets = output / "assets/guide"
    assets.mkdir(parents=True)
    for name in ("guide.css", "guide.js"):
        shutil.copyfile(guide / name, assets / name)


def build_site(
    repo_root: Path,
    output: Path,
    versions_branch: str | None = None,
    landing_source: Path | None = None,
) -> Path:
    repo = Path(repo_root).resolve()
    source = Path(landing_source).resolve() if landing_source else repo
    output = _validate_output(repo, source, Path(output).absolute())
    version_data = _read_versions(repo, versions_branch) if versions_branch else None
    output.parent.mkdir(parents=True, exist_ok=True)
    # Prepare completely before touching the previous successful artifact.
    with tempfile.TemporaryDirectory(
        prefix=".pages-stage-", dir=output.parent,
    ) as temporary:
        staged = Path(temporary) / "site"
        docs = staged / "docs"
        docs.mkdir(parents=True)
        if version_data:
            commit, versions, preferred = version_data
            _export_versions(repo, docs, commit, versions, preferred)
            prefix = f"docs/{preferred}/"
        else:
            subprocess.run(  # noqa: S603 -- current Python, fixed module, validated paths
                [
                    sys.executable,
                    "-m",
                    "mkdocs",
                    "build",
                    "--strict",
                    "--config-file",
                    str(repo / "mkdocs.yml"),
                    "--site-dir",
                    str(docs),
                ],
                cwd=repo,
                check=True,
            )
            _guide_redirect(docs, "../../")
            prefix = "docs/"
        _landing(source, staged, prefix)
        for path in staged.rglob("*"):
            if _is_link(path):
                raise ValueError(f"Pages artifacts cannot contain symlinks: {path}")
        (staged / BUILD_MARKER).write_text(
            "Generated by scripts/docs/build_site.py\n", encoding="utf-8",
        )
        (staged / ".nojekyll").touch()
        _validate_output(repo, source, output)
        if output.exists():
            shutil.rmtree(output)
        staged.replace(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("site"))
    parser.add_argument(
        "--versions-branch", help="Git ref containing the Mike version archive",
    )
    parser.add_argument(
        "--landing-source", type=Path, help="Checkout supplying docs/guide (e.g. main)",
    )
    args = parser.parse_args()
    try:
        output = build_site(
            Path(__file__).resolve().parents[2],
            args.output,
            args.versions_branch,
            args.landing_source,
        )
    except (
        ValueError,
        OSError,
        subprocess.CalledProcessError,
        tarfile.TarError,
    ) as error:
        parser.exit(1, f"Pages build failed: {error}\n")
    print(f"Pages artifact: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
