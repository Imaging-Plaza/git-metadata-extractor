from src.v2._compat import warn_legacy_import
from src.v2.quality import *  # noqa: F403
from src.v2.quality import __all__ as __all__

warn_legacy_import("src.v2.validation", "src.v2.quality")
