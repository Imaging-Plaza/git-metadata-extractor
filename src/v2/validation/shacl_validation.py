from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.quality import shacl_validation as _module

warn_legacy_import("src.v2.validation.shacl_validation", "src.v2.quality.shacl_validation")

sys.modules[__name__] = _module
