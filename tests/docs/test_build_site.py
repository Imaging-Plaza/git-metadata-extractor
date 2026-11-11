"""Pages assembly contracts, using real Git archives and a tiny MkDocs project."""

# Standalone stdlib discovery keeps docs checks independent of runtime/pytest deps.
# ruff: noqa: INP001, PT009, PT027

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/docs/build_site.py"


class BuildSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert SCRIPT.is_file(), "The Pages assembly CLI must exist"
        spec = importlib.util.spec_from_file_location("build_site", SCRIPT)
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.output = self.base / "public"
        self.write(
            "docs/guide/index.html",
            """<!doctype html><html lang="en"><head>
<link href="guide.css" rel="stylesheet"><script src="guide.js" defer></script>
</head><body><a href="#architecture">Architecture</a>
<a href="../" data-doc="index.md">Docs</a>
<a href="../nested/page/#details" data-doc="nested/page.md#details">Detail</a>
<a href="../nested/" data-doc="nested/index.md?view=full#intro">Nested</a>
<a href="https://example.com/">External</a></body></html>""",
        )
        self.write("docs/guide/guide.css", "body { color: navy; }")
        self.write("docs/guide/guide.js", "/* local enhancement */")

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def git(self, *args):
        return subprocess.run(  # noqa: S603 -- controlled fixture args
            ["git", "-C", str(self.repo), *args],  # noqa: S607 -- fixed Git executable
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def archive(self, versions=None):
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.versions = (
            versions
            if versions is not None
            else [
                {"version": "1.0", "title": "1.0", "aliases": ["stable"]},
                {"version": "dev", "title": "Development", "aliases": ["latest"]},
            ]
        )
        self.write("versions.json", json.dumps(self.versions))
        for version in self.versions:
            name = version["version"]
            if "/" in name or "\\" in name or name in {".", ".."}:
                continue
            self.write(f"{name}/index.html", f"Reference {name}")
            self.write(f"{name}/guide/index.html", "Old visual guide")
            self.write(f"{name}/search/search_index.json", '{"docs": []}')
            self.write(f"{name}/nested/page/index.html", "Reference details")
        self.write("unrelated-secret.txt", "must not publish")
        self.write("orphan/index.html", "must not publish")
        self.git("add", ".")
        self.git("commit", "-qm", "Fixture archive")
        self.git("branch", "gh-pages")

    def build(self, **kwargs):
        return self.builder.build_site(self.repo, self.output, **kwargs)

    def test_exports_declared_versions_and_materializes_aliases(self):
        self.archive()
        self.build(versions_branch="gh-pages")
        self.assertEqual(
            (self.output / "docs/dev/index.html").read_text(), "Reference dev",
        )
        self.assertEqual(
            (self.output / "docs/latest/index.html").read_text(), "Reference dev",
        )
        self.assertEqual(
            (self.output / "docs/stable/index.html").read_text(), "Reference 1.0",
        )
        self.assertEqual(
            json.loads((self.output / "docs/versions.json").read_text()), self.versions,
        )
        self.assertFalse((self.output / "docs/orphan").exists())
        self.assertFalse((self.output / "docs/unrelated-secret.txt").exists())
        self.assertFalse(any(path.is_symlink() for path in self.output.rglob("*")))
        self.assertIn('href="latest/"', (self.output / "docs/index.html").read_text())

    def test_landing_links_assets_and_guide_redirect_keep_fragments(self):
        self.archive()
        self.build(versions_branch="gh-pages")
        landing = (self.output / "index.html").read_text()
        for expected in [
            'href="docs/latest/"',
            'href="docs/latest/nested/page/#details"',
            'href="docs/latest/nested/?view=full#intro"',
            'href="#architecture"',
            'href="https://example.com/"',
            'href="assets/guide/guide.css"',
            'src="assets/guide/guide.js"',
            'data-built-landing="true"',
        ]:
            self.assertIn(expected, landing)
        self.assertTrue((self.output / "assets/guide/guide.css").is_file())
        redirect = (self.output / "docs/latest/guide/index.html").read_text()
        self.assertIn('href="../../../"', redirect)
        self.assertIn("location.search", redirect)
        self.assertIn("location.hash", redirect)

    def test_preferred_version_falls_back_to_stable_then_first(self):
        for versions, preferred in [
            ([{"version": "2.0", "aliases": ["stable"]}], "stable"),
            ([{"version": "2.0", "aliases": []}], "2.0"),
        ]:
            with self.subTest(preferred=preferred):
                self.archive(versions)
                self.build(versions_branch="gh-pages")
                self.assertIn(
                    f'href="docs/{preferred}/"',
                    (self.output / "index.html").read_text(),
                )
                # The fixture repository can be recreated for the next case.
                self.git("branch", "-D", "gh-pages")

    def test_landing_can_come_from_a_different_checkout(self):
        self.archive()
        main = self.base / "main"
        guide = main / "docs/guide"
        guide.mkdir(parents=True)
        for name in ("index.html", "guide.css", "guide.js"):
            (guide / name).write_text((self.repo / "docs/guide" / name).read_text())
        (guide / "index.html").write_text(
            "<html><head></head><body>Main landing</body></html>",
        )
        self.build(versions_branch="gh-pages", landing_source=main)
        self.assertIn("Main landing", (self.output / "index.html").read_text())

    def test_empty_or_absent_versions_fail_before_replacing_output(self):
        self.archive()
        self.build(versions_branch="gh-pages")
        original = (self.output / "index.html").read_bytes()
        self.write("versions.json", "[]")
        self.git("add", "versions.json")
        self.git("commit", "-qm", "Empty archive")
        self.git("branch", "-f", "gh-pages")
        with self.assertRaises(ValueError):
            self.build(versions_branch="gh-pages")
        self.assertEqual((self.output / "index.html").read_bytes(), original)
        self.git("rm", "versions.json")
        self.git("commit", "-qm", "Absent version metadata")
        self.git("branch", "-f", "gh-pages")
        with self.assertRaises(subprocess.CalledProcessError):
            self.build(versions_branch="gh-pages")
        self.assertEqual((self.output / "index.html").read_bytes(), original)

    def test_unsafe_output_locations_are_refused_without_deletion(self):
        self.archive()
        sentinel = self.write("docs/keep.txt", "keep")
        for output in [
            self.repo,
            self.base,
            self.repo / "docs",
            self.repo / "docs/generated",
            self.repo / ".git",
            self.repo / "docs/guide",
        ]:
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.builder.build_site(self.repo, output, versions_branch="gh-pages")
            self.assertEqual(sentinel.read_text(), "keep")

    def test_output_can_be_rebuilt_but_unrelated_directory_is_preserved(self):
        self.archive()
        self.build(versions_branch="gh-pages")
        (self.output / "stale.txt").write_text("old")
        self.build(versions_branch="gh-pages")
        self.assertFalse((self.output / "stale.txt").exists())
        other = self.base / "other"
        other.mkdir()
        (other / "keep.txt").write_text("keep")
        with self.assertRaises(ValueError):
            self.builder.build_site(self.repo, other, versions_branch="gh-pages")
        self.assertTrue((other / "keep.txt").exists())

    def test_rejects_unsafe_or_colliding_version_names(self):
        for versions in [
            [{"version": "../escape", "aliases": []}],
            [{"version": "ok", "aliases": [".."]}],
            [{"version": "ok", "aliases": ["ok"]}],
            [{"version": "ok", "aliases": ["versions.json"]}],
        ]:
            with self.subTest(versions=versions):
                self.archive(versions)
                with self.assertRaises(ValueError):
                    self.build(versions_branch="gh-pages")
                self.git("branch", "-D", "gh-pages")

    def test_missing_declared_version_fails(self):
        self.archive()
        self.git("rm", "-r", "dev")
        self.git("commit", "-qm", "Remove version")
        self.git("branch", "-f", "gh-pages")
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            self.build(versions_branch="gh-pages")

    def test_git_archive_symlinks_are_rejected_but_alias_links_are_not_exported(self):
        self.archive()
        blob = subprocess.run(  # noqa: S603 -- fixed fixture command
            ["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],  # noqa: S607 -- fixed Git executable
            input="dev",
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.git("update-index", "--add", "--cacheinfo", f"120000,{blob},latest")
        self.git("commit", "-qm", "Mike symlink alias")
        self.git("branch", "-f", "gh-pages")
        self.build(versions_branch="gh-pages")
        self.assertFalse((self.output / "docs/latest").is_symlink())
        self.assertEqual(
            (self.output / "docs/latest/index.html").read_text(), "Reference dev",
        )
        self.git(
            "update-index", "--add", "--cacheinfo", f"120000,{blob},dev/unsafe-link",
        )
        self.git("commit", "-qm", "Link within version")
        self.git("branch", "-f", "gh-pages")
        with self.assertRaises(ValueError):
            self.build(versions_branch="gh-pages")

    def test_archive_traversal_and_links_are_rejected(self):
        for name, kind in [
            ("../escape", tarfile.REGTYPE),
            ("dev/../../escape", tarfile.REGTYPE),
            ("dev\\escape", tarfile.REGTYPE),
            ("/absolute", tarfile.REGTYPE),
            ("dev/link", tarfile.SYMTYPE),
            ("dev/link", tarfile.LNKTYPE),
        ]:
            with self.subTest(name=name, kind=kind):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.linkname = "../../escape"
                    archive.addfile(entry)
                stream.seek(0)
                with tarfile.open(fileobj=stream) as archive, self.assertRaises(
                    ValueError,
                ):
                    self.builder.extract_archive(archive, self.base / "extracted")
        self.assertFalse((self.base / "escape").exists())

    @unittest.skipUnless(
        importlib.util.find_spec("mkdocs"), "MkDocs docs extra is not installed",
    )
    def test_local_mode_builds_strict_mkdocs_without_a_git_repository(self):
        self.write("mkdocs.yml", "site_name: Fixture\ndocs_dir: docs\n")
        self.write("docs/index.md", "# Reference home\n")
        self.write("docs/nested/page.md", "# Details\n")
        self.build()
        self.assertIn("Reference home", (self.output / "docs/index.html").read_text())
        self.assertIn('href="docs/"', (self.output / "index.html").read_text())
        self.assertIn(
            'href="../../"', (self.output / "docs/guide/index.html").read_text(),
        )
        self.assertTrue((self.output / "docs/search/search_index.json").is_file())

    @unittest.skipUnless(
        importlib.util.find_spec("mkdocs"), "MkDocs docs extra is not installed",
    )
    def test_strict_build_failure_preserves_the_previous_artifact(self):
        self.write("mkdocs.yml", "site_name: Fixture\ndocs_dir: docs\n")
        self.write("docs/index.md", "# Reference home\n")
        self.build()
        original = (self.output / "docs/index.html").read_bytes()
        self.write("docs/index.md", "# Invalid reference\n[Missing](missing.md)\n")
        with self.assertRaises(subprocess.CalledProcessError):
            self.build()
        self.assertEqual((self.output / "docs/index.html").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
