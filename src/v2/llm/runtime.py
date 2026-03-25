from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.agents.llm import runtime as _module

warn_legacy_import("src.v2.llm.runtime", "src.v2.agents.llm.runtime")

sys.modules[__name__] = _module
