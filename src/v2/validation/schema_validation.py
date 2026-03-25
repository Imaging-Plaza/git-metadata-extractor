from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.quality import schema_validation as _module

warn_legacy_import("src.v2.validation.schema_validation", "src.v2.quality.schema_validation")

sys.modules[__name__] = _module
