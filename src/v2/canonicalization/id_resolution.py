from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.normalizers import id_resolution as _module

warn_legacy_import("src.v2.canonicalization.id_resolution", "src.v2.normalizers.id_resolution")

sys.modules[__name__] = _module
