from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.ingest.detection import models as _module

warn_legacy_import("src.v2.detection.models", "src.v2.ingest.detection.models")

sys.modules[__name__] = _module
