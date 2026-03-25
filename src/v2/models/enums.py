from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.api_models import enums as _module

warn_legacy_import("src.v2.models.enums", "src.v2.api_models.enums")

sys.modules[__name__] = _module
