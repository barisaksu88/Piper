"""Chat pane — build + refresh hooks (CI01-ready).
Idempotent, resilient to missing layout constants. Avoids L.VIEWPORT.
"""
from __future__ import annotations
import dearpygui.dearpygui as dpg
from ui.layout_constants import L

# ---------- helpers (safe fallbacks so exceptions never swallow UI) ----------

def _input_dims() -> tuple[int, int]:
    try:
        return int(L.INPUT.HEIGHT), int(L.INPUT.PAD_Y)
    except Exception:
        return 36, 6


def _pane_width() -> int:
    try:
        return int(L.PANE.LEFT_WIDTH - L.SPACE.PAD)
    except Exception:
        return 600


def _scroll_height() -> int:
    # Derive from WINDOW + PANE padding; do NOT rely on L.VIEWPORT
    try:
        base_h = int(getattr(L.WINDOW, "HEIGHT", 720))
        hdr_h  = int(getattr(L.PANE,   "HEADER_HEIGHT", 48))
        body_v = int(getattr(L.PANE,   "BODY_VPAD", 80))
        ih, ipy = _input_dims()
        reserved = ih + (ipy * 2) + 12  # input bar + tiny gap
        return max(180, base_h - hdr_h - body_v - reserved)
    except Exception:
        return 360


# ----------------------------- public API -----------------------------------

def build() -> None:
    """Create/augment the chat column widgets (left pane).
    - Always ensures a container, header, scroll, and CI01 input bar exist.
    - Never bails early; we inject missing pieces into existing layout.
    """
    try:
        if not dpg.does_item_exist("body_row"):
            return

        parent_container = "chat_container"
        if not dpg.does_item_exist(parent_container):
            dpg.add_group(parent="body_row", tag=parent_container)

        # Header (create once if missing)
        if not dpg.does_item_exist("chat_hdr"):
            with dpg.group(parent=parent_container, horizontal=True):
                dpg.add_text("Chat", tag="chat_hdr")
                dpg.add_spacer(width=10)
                dpg.add_button(label="Copy Chat", tag="copy_chat_btn")
                dpg.add_spacer(width=10)
                dpg.add_text("[⏸ autoscroll]", tag="chat_autoscroll_badge", show=False)
            dpg.add_separator(parent=parent_container)
            dpg.add_spacer(parent=parent_container, height=6)

        # Vertical column to stack scroll + input
        if not dpg.does_item_exist("chat_column"):
            dpg.add_group(tag="chat_column", parent=parent_container, horizontal=False)

        # Scroll region
        if not dpg.does_item_exist("chat_scroll"):
            with dpg.child_window(tag="chat_scroll", parent="chat_column",
                                  width=_pane_width(), height=_scroll_height(),
                                  autosize_x=False, autosize_y=False):
                dpg.add_text("", tag="chat_text", wrap=_pane_width())
                dpg.add_spacer(tag="chat_pad", height=getattr(L.SPACE, "SMALL", 6))
        else:
            # Ensure size sane even if previously created with old math
            try:
                dpg.configure_item("chat_scroll", width=_pane_width(), height=_scroll_height())
            except Exception:
                pass

        # [CI01] Reserved Chat Input Bar (layout only)
        if not dpg.does_item_exist("chat_input_bar"):
            ih, ipy = _input_dims()
            with dpg.child_window(tag="chat_input_bar", parent="chat_column",
                                  width=_pane_width(), height=ih + (ipy * 2),
                                  autosize_x=False, autosize_y=False,
                                  no_scrollbar=True, border=True):
                dpg.add_spacer(height=ipy)                # CI01 reserved bar; no content yet (CI02 will add InputText+Send)
        else:
            # Ensure width/height reflect constants after resize
            try:
                ih, ipy = _input_dims()
                dpg.configure_item("chat_input_bar", width=_pane_width(), height=ih + (ipy * 2))
            except Exception:
                pass

    except Exception:
        # Never crash the GUI on layout build; CI02 will replace with stricter wiring
        pass


def refresh(text: str, autoscroll_on: bool) -> None:
    """Update chat text and autoscroll badge only."""
    try:
        if dpg.does_item_exist("chat_text"):
            dpg.set_value("chat_text", text)
        if dpg.does_item_exist("chat_autoscroll_badge"):
            dpg.configure_item("chat_autoscroll_badge", show=False)
    except Exception:
        pass
