from src.v2._compat import warn_legacy_import
from src.v2.ingest.detection import *  # noqa: F403
from src.v2.ingest.detection import __all__ as __all__

warn_legacy_import("src.v2.detection", "src.v2.ingest.detection")
