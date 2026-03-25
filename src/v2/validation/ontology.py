from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.quality import ontology as _module

warn_legacy_import("src.v2.validation.ontology", "src.v2.quality.ontology")

sys.modules[__name__] = _module
