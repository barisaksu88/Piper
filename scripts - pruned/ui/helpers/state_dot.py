# scripts/ui/helpers/state_dot.py
from __future__ import annotations
import dearpygui.dearpygui as dpg

# Known/legacy tag names we may find for the status dot
_DOT_TAGS = ("state_dot_circle", "state_dot", "status_dot")

def update_state_dot(state_text: str) -> None:
    """
    Update the header status dot color based on state_text.
    Safe no-op if no dot exists.
    """
    try:
        # resolve dot tag
        dot = None
        for t in _DOT_TAGS:
            try:
                if dpg.does_item_exist(t):
                    dot = t
                    break
            except Exception:
                pass
        if not dot:
            return

        # pick a color; keep your existing mapping simple/neutral
        st = (state_text or "").lower()
        name = (state_text or "").strip().upper()
        r, g, b = resolve_color(name)
        col = (r, g, b, 255)

        # apply
        try:
            dpg.configure_item(dot, fill=col)
        except Exception:
            pass
    except Exception:
        pass

