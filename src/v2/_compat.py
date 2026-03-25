from __future__ import annotations

import warnings


def warn_legacy_import(old_path: str, new_path: str) -> None:
    """Emit a deprecation warning for legacy v2 module imports."""
    warnings.warn(
        f"`{old_path}` is deprecated and will be removed in a future release; "
        f"use `{new_path}` instead.",
        DeprecationWarning,
        stacklevel=2,
    )
