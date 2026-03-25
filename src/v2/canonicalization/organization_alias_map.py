from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.normalizers import organization_alias_map as _module

warn_legacy_import("src.v2.canonicalization.organization_alias_map", "src.v2.normalizers.organization_alias_map")

sys.modules[__name__] = _module
