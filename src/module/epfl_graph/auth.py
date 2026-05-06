"""EPFL Graph authentication.

Builds the `login_info` dict that the upstream `graphai_client` library
expects, sourcing credentials from the EPFL_GRAPH_USERNAME and
EPFL_GRAPH_PASSWORD env vars.

The upstream `login()` helper expects a path to a JSON file on disk so it
can re-authenticate on 401 responses. We materialise that file lazily into
a chmod-600 tempfile and clean it up at process exit.
"""

from __future__ import annotations

import atexit
import json
import os
import tempfile
import threading
from typing import Optional

from graphai_client.client_api.utils import login as _upstream_login

DEFAULT_HOST = "https://graphai.epfl.ch"
DEFAULT_PORT = 443

_lock = threading.Lock()
_credentials_path: Optional[str] = None
_login_info: Optional[dict] = None


def _read_credentials() -> tuple[str, str]:
    user = os.environ.get("EPFL_GRAPH_USERNAME")
    password = os.environ.get("EPFL_GRAPH_PASSWORD")
    missing = [
        name
        for name, value in (
            ("EPFL_GRAPH_USERNAME", user),
            ("EPFL_GRAPH_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing),
        )
    return user, password  # type: ignore[return-value]


def _ensure_credentials_file() -> str:
    """Write a chmod-600 tempfile with the JSON the upstream login expects."""
    global _credentials_path
    if _credentials_path is not None and os.path.exists(_credentials_path):
        return _credentials_path

    user, password = _read_credentials()
    host = os.environ.get("EPFL_GRAPH_HOST", DEFAULT_HOST)
    port = int(os.environ.get("EPFL_GRAPH_PORT", str(DEFAULT_PORT)))

    fd, path = tempfile.mkstemp(prefix="epfl_graph_", suffix=".json")
    try:
        os.close(fd)
        os.chmod(path, 0o600)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(
                {"host": host, "port": port, "user": user, "password": password},
                fp,
            )
    except Exception:
        if os.path.exists(path):
            os.unlink(path)
        raise

    _credentials_path = path
    atexit.register(_cleanup_credentials_file)
    return path


def _cleanup_credentials_file() -> None:
    global _credentials_path
    if _credentials_path and os.path.exists(_credentials_path):
        try:
            os.unlink(_credentials_path)
        finally:
            _credentials_path = None


def get_login_info(force_refresh: bool = False) -> dict:
    """Return a `login_info` dict ready for graphai_client API calls.

    The result is cached for the lifetime of the process. Pass
    `force_refresh=True` to discard the cache and re-authenticate.
    """
    global _login_info
    with _lock:
        if force_refresh:
            _login_info = None
        if _login_info is None:
            credentials_path = _ensure_credentials_file()
            _login_info = _upstream_login(credentials_path)
        return _login_info


def reset_login_info() -> None:
    """Drop the cached login_info and credentials file (next call re-auths)."""
    global _login_info
    with _lock:
        _login_info = None
        _cleanup_credentials_file()
