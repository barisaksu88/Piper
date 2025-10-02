# -*- coding: utf-8 -*-
"""
viewport_utils.py â€” safe viewport helpers (B04.17 clean)
No top-level try blocks. All error handling is inside functions.
"""

from __future__ import annotations
from typing import Optional

def calibrate_to_viewport() -> None:
    """
    Best-effort: nudge layout based on current viewport client size.
    Safe no-op if DearPyGui is unavailable or viewport calls fail.
    """
    try:
        import dearpygui.dearpygui as dpg  # local import to avoid hard dependency at import time

        # Read current client size; guard every call
        try:
            w = dpg.get_viewport_client_width()
        except Exception:
            w = None
        try:
            h = dpg.get_viewport_client_height()
        except Exception:
            h = None

        # If we have dimensions, you can add tiny heuristics here (kept minimal/neutral):
        # (Do NOT introduce new layout literals; respect layout_constants via panes construction.)
        if w is None and h is None:
            return

        # Example gentle refresh hooks (all guarded):
        # - force a frame callback to reflow scroll positions if needed
        try:
            dpg.set_frame_callback(
                dpg.get_frame_count() + 1,
                lambda s=None, a=None: None  # placeholder no-op; real logic lives in panes/helpers
            )
        except Exception:
            pass

    except Exception:
        # Any unexpected failure is swallowed to keep GUI resilient
        pass

