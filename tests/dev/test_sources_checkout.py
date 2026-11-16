"""Bootstrap must never overwrite an existing development checkout."""

# Bootstrap tests run before installing pytest or either application's dependencies.
# ruff: noqa: INP001, PT009, PT027

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.dev.sources_checkout import ensure_checkout, load_lock


def git(path, *args):
    return subprocess.check_output(  # noqa: S603 - fixed Git, temporary test repositories
        ["git", "-C", str(path), *args],  # noqa: S607 - developer's Git on PATH
        text=True,
        stderr=subprocess.PIPE,
    ).strip()


class SourcesCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "--quiet")
        git(self.origin, "config", "user.email", "test@example.invalid")
        git(self.origin, "config", "user.name", "Test")
        (self.origin / "tracked.txt").write_text("first")
        git(self.origin, "add", ".")
        git(self.origin, "commit", "--quiet", "-m", "first")
        self.revision = git(self.origin, "rev-parse", "HEAD")
        (self.origin / "tracked.txt").write_text("second")
        git(self.origin, "commit", "--quiet", "-am", "second")
        self.checkout = self.root / "dev" / "open-pulse-sources"

    def setup_checkout(self, **kwargs):
        ensure_checkout(self.checkout, str(self.origin), self.revision, **kwargs)

    def test_fetches_exact_revision_not_branch_tip_and_is_idempotent(self):
        self.setup_checkout()
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), self.revision)
        self.setup_checkout(check=True)
        self.setup_checkout()

    def test_dirty_checkout_is_preserved_and_rejected(self):
        self.setup_checkout()
        (self.checkout / "tracked.txt").write_text("local work")
        with self.assertRaisesRegex(RuntimeError, "uncommitted"):
            self.setup_checkout()
        self.assertEqual((self.checkout / "tracked.txt").read_text(), "local work")

    def test_wrong_revision_is_not_reset(self):
        self.setup_checkout()
        git(self.checkout, "fetch", "origin", "HEAD")
        git(
            self.checkout, "checkout", "--detach", git(self.origin, "rev-parse", "HEAD"),
        )
        before = git(self.checkout, "rev-parse", "HEAD")
        with self.assertRaisesRegex(RuntimeError, "revision"):
            self.setup_checkout()
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), before)

    def test_wrong_origin_is_rejected(self):
        self.setup_checkout()
        git(self.checkout, "remote", "set-url", "origin", str(self.root / "different"))
        with self.assertRaisesRegex(RuntimeError, "origin"):
            self.setup_checkout()

    def test_non_repository_directory_is_preserved(self):
        self.checkout.mkdir(parents=True)
        (self.checkout / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(RuntimeError, "Git checkout"):
            self.setup_checkout()
        self.assertTrue((self.checkout / "keep.txt").exists())

    def test_check_does_not_create_missing_checkout(self):
        with self.assertRaisesRegex(RuntimeError, "missing"):
            self.setup_checkout(check=True)
        self.assertFalse(self.checkout.exists())

    def test_missing_revision_fails_without_using_branch_tip(self):
        with self.assertRaisesRegex(RuntimeError, "Publish that revision"):
            ensure_checkout(self.checkout, str(self.origin), "a" * 40)
        self.assertTrue((self.checkout / ".git").exists())
        self.assertFalse((self.checkout / "tracked.txt").exists())

    def test_failed_initial_fetch_can_resume_only_when_empty(self):
        with self.assertRaisesRegex(RuntimeError, "Publish that revision"):
            ensure_checkout(self.checkout, str(self.origin), "a" * 40)
        self.setup_checkout()
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), self.revision)

    def test_incomplete_checkout_with_ignored_files_is_not_changed(self):
        with self.assertRaisesRegex(RuntimeError, "Publish that revision"):
            ensure_checkout(self.checkout, str(self.origin), "a" * 40)
        (self.checkout / ".git/info/exclude").write_text("local-data\n")
        (self.checkout / "local-data").write_text("keep")
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            self.setup_checkout()
        self.assertEqual((self.checkout / "local-data").read_text(), "keep")

    def test_lock_rejects_moving_revision_and_missing_fields(self):
        path = self.root / "sources.lock.json"
        for payload in (
            {},
            {
                "repository": "https://github.com/example/repo",
                "revision": "main",
                "version": "1.0",
            },
        ):
            path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                load_lock(path)


if __name__ == "__main__":
    unittest.main()
