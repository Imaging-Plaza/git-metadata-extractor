from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.ingest.providers import rate_limiter as _module

warn_legacy_import("src.v2.providers.rate_limiter", "src.v2.ingest.providers.rate_limiter")

sys.modules[__name__] = _module
