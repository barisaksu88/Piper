# Thin composer: compose panes and delegate to pane_parts (≤150 lines)
from __future__ import annotations
import dearpygui.dearpygui as dpg
from ui.layout_constants import L
from ui.pane_parts import header_bar as _hb
from ui.helpers.theme_utils import ensure_pane_theme as _ensure_pane_theme
from ui.pane_parts.chat_column import build as build_chat_column
from ui.pane_parts.logs_avatar_column import build as build_logs_avatar_column


def init_ui(log_path: str) -> None:
    """Compose the Piper GUI. No heavy logic here."""
    if dpg is None:
        return

    # Reset root
    if dpg.does_item_exist("root"):
        dpg.delete_item("root")

    root = dpg.add_window(
        tag="root",
        pos=(L.WINDOW.OFFSET_X, L.WINDOW.OFFSET_Y),
        no_title_bar=True,
        no_move=True,
        no_resize=True,
        no_scrollbar=True,
    )
    dpg.set_item_width("root", L.WINDOW.WIDTH)
    dpg.set_item_height("root", L.WINDOW.HEIGHT)
    dpg.set_primary_window("root", True)

    pane_theme = _ensure_pane_theme()
    dpg.bind_item_theme("root", pane_theme)

    # Header
    if not dpg.does_item_exist("header_row"):
        header_row = dpg.add_group(parent=root, tag="header_row", horizontal=True)
        _hb.build(parent=header_row, log_path=log_path)
        dpg.add_spacer(parent=root, height=L.HEADER.BOTTOM_GAP)

    # Body row: Left (Chat) + Right (Logs/Avatar)
    with dpg.group(parent=root, tag="body_row", horizontal=True):
        dpg.add_spacer(width=L.CHAT.INSET_LEFT)
        build_chat_column(parent="body_row", pane_theme=pane_theme)
        build_logs_avatar_column(parent="body_row", pane_theme=pane_theme, log_path=log_path)


# Compatibility shim for ui.panes import
from ui.helpers.refresh_core import refresh_ui as _refresh_core


def refresh_ui(state_text: str,
               heartbeat_text: str,
               chat_text: str,
               log_text: str,
               chat_dirty: bool,
               log_dirty: bool) -> None:
    """Delegate to the authoritative refresh_core implementation (R2: no logic here)."""
    try:
        _refresh_core(state_text, heartbeat_text, chat_text, log_text, chat_dirty, log_dirty)
    except Exception:
        # Keep GUI resilient even if refresh fails
        pass
