from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.schema.models import strict as _module

warn_legacy_import("src.v2.generated.entities", "src.v2.schema.models.strict")

sys.modules[__name__] = _module

