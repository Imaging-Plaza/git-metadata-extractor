"""High-level integrated EPFL Graph workflows.

Wraps `graphai_client.client.process_video`, the end-to-end pipeline that
downloads a video, extracts audio + slides, transcribes, runs OCR and
translates everything into the requested destination languages.
"""

from __future__ import annotations

from typing import Any, Optional

from graphai_client import client as _client

from src.module.epfl_graph.auth import get_login_info


def process_video(
    video_url: str,
    login_info: Optional[dict] = None,
    **kwargs: Any,
):
    return _client.process_video(
        video_url=video_url, login_info=login_info or get_login_info(), **kwargs,
    )
