from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.agents.rule_based import membership_agent as _module

warn_legacy_import(
    "src.v2.agents.membership_agent",
    "src.v2.agents.rule_based.membership_agent",
)

sys.modules[__name__] = _module
