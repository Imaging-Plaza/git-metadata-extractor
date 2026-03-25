from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.quality import crossref as _module

warn_legacy_import("src.v2.validation.crossref", "src.v2.quality.crossref")

sys.modules[__name__] = _module
