from __future__ import annotations

import re
from pathlib import Path

LEGACY_IMPORT_PATTERN = re.compile(
    r"src\.v2\.(providers|detection|canonicalization|validation|models|generated|llm\.runtime)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = [REPO_ROOT / "src", REPO_ROOT / "tests", REPO_ROOT / "scripts"]
ALLOWLIST_PREFIXES = [
    REPO_ROOT / "src" / "v2" / "providers",
    REPO_ROOT / "src" / "v2" / "detection",
    REPO_ROOT / "src" / "v2" / "canonicalization",
    REPO_ROOT / "src" / "v2" / "validation",
    REPO_ROOT / "src" / "v2" / "models",
    REPO_ROOT / "src" / "v2" / "generated",
]
ALLOWLIST_FILES = {
    REPO_ROOT / "src" / "v2" / "llm" / "__init__.py",
    REPO_ROOT / "src" / "v2" / "llm" / "runtime.py",
    REPO_ROOT / "src" / "v2" / "agents" / "article_agent.py",
    REPO_ROOT / "src" / "v2" / "agents" / "contribution_agent.py",
    REPO_ROOT / "src" / "v2" / "agents" / "membership_agent.py",
    REPO_ROOT / "src" / "v2" / "agents" / "organization_agent.py",
    REPO_ROOT / "src" / "v2" / "agents" / "person_agent.py",
    REPO_ROOT / "src" / "v2" / "agents" / "repository_agent.py",
    REPO_ROOT / "tests" / "v2" / "test_import_compat_shims.py",
}


def _is_allowlisted(path: Path) -> bool:
    if path in ALLOWLIST_FILES:
        return True
    return any(path.is_relative_to(prefix) for prefix in ALLOWLIST_PREFIXES)


def test_no_new_legacy_imports_outside_compat_shims() -> None:
    violations: list[str] = []

    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if _is_allowlisted(path):
                continue

            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if LEGACY_IMPORT_PATTERN.search(line):
                    relative = path.relative_to(REPO_ROOT)
                    violations.append(f"{relative}:{line_number}:{line.strip()}")

    assert not violations, "Found forbidden legacy imports:\n" + "\n".join(sorted(violations))
