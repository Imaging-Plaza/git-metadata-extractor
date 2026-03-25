from __future__ import annotations

import sys

from src.v2._compat import warn_legacy_import
from src.v2.ingest.providers import mock_orcid as _module

warn_legacy_import("src.v2.providers.mock_orcid", "src.v2.ingest.providers.mock_orcid")

sys.modules[__name__] = _module
