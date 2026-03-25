from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.ingest.providers import base as _module

warn_legacy_import("src.v2.providers.base", "src.v2.ingest.providers.base")

sys.modules[__name__] = _module
