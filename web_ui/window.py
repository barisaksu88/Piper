"""web_ui.window

Optional desktop window wrapper for the Piper Web UI.

Uses pywebview to open a dedicated app window pointing at the backend-served
frontend URL.  If pywebview is not installed, logs a graceful message and
falls back to browser-only access.

Constraints:
- Must not crash if pywebview is missing.
- Must not block backend startup.
- Must not import from ui/, core/, memory/, tools/, or app.py.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

_LOG = logging.getLogger("web_ui.window")


def open_piper_window(url: str, width: int = 1280, height: int = 820) -> None:
    """Open a desktop window for the Piper Web UI.

    This function blocks while the window is open.  Call it from a
    daemon thread so the backend continues to run.
    """
    try:
        import webview  # type: ignore[import-untyped]
    except ImportError:
        _LOG.warning(
            "pywebview is not installed; open %s manually in a browser",
            url,
        )
        return

    try:
        webview.create_window(
            title="Piper",
            url=url,
            width=width,
            height=height,
            resizable=True,
        )
        _LOG.info("Opening Piper desktop window: %s", url)
        webview.start()
        _LOG.info("Piper desktop window closed")
    except Exception as exc:
        _LOG.error("Failed to open desktop window: %s", exc)


def launch_window_thread(url: str, width: int = 1280, height: int = 820) -> threading.Thread:
    """Start ``open_piper_window`` in a daemon thread and return the thread."""
    thread = threading.Thread(
        target=open_piper_window,
        args=(url, width, height),
        daemon=True,
        name="piper-webview",
    )
    thread.start()
    return thread
