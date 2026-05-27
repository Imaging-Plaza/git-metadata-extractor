from __future__ import annotations

import os

# Top-level reads in `src/v1/parsers/{users,orgs}_parser.py` previously hard-failed
# with KeyError when GME_GITHUB_TOKEN was unset. The token-pool refactor relaxes
# that to a tolerant `.get(...)`, but tests for the legacy import path still need
# a default so collection doesn't depend on a developer-only env.
os.environ.setdefault("GME_GITHUB_TOKEN", "ci-test-github-token")
