"""Image-related EPFL Graph endpoints (OCR on slides)."""

from __future__ import annotations

from typing import Any, Optional

from graphai_client.client_api import image as _image

from src.module.epfl_graph.auth import get_login_info


def extract_text_from_slide(
    slide_token: str,
    login_info: Optional[dict] = None,
    **kwargs: Any,
) -> Optional[dict]:
    return _image.extract_text_from_slide(
        slide_token=slide_token, login_info=login_info or get_login_info(), **kwargs,
    )
