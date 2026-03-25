from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.ingest.providers import orcid_provider as _module

warn_legacy_import("src.v2.providers.orcid_provider", "src.v2.ingest.providers.orcid_provider")

sys.modules[__name__] = _module
