"""Audio-related EPFL Graph endpoints (transcription, language detection)."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from graphai_client.client_api import voice as _voice

from src.module.epfl_graph.auth import get_login_info


def transcribe_audio(
    audio_token: str,
    login_info: Optional[dict] = None,
    **kwargs: Any,
) -> Tuple[Optional[str], Optional[List[dict]]]:
    return _voice.transcribe_audio(
        audio_token=audio_token, login_info=login_info or get_login_info(), **kwargs,
    )


def detect_audio_language(
    audio_token: str,
    login_info: Optional[dict] = None,
    **kwargs: Any,
) -> Optional[str]:
    """Detect the language of the voice in the audio (`voice.detect_language`)."""
    return _voice.detect_language(
        audio_token=audio_token, login_info=login_info or get_login_info(), **kwargs,
    )
