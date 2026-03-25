from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.ingest.detection import github_url_classifier as _module

warn_legacy_import("src.v2.detection.github_url_classifier", "src.v2.ingest.detection.github_url_classifier")

sys.modules[__name__] = _module
