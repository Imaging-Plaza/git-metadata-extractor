from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.normalizers import string_utils as _module

warn_legacy_import("src.v2.canonicalization.string_utils", "src.v2.normalizers.string_utils")

sys.modules[__name__] = _module
