"""
B04.9 Chat/Log writer helpers â€” extracted from ui/panes.py
No behavior change; belt-and-suspenders writes preserved.
"""
from __future__ import annotations
import dearpygui.dearpygui as dpg

def write_chat(chat_text: str, chat_scroll_tag: str, chat_text_tag: str, chat_was_bottom: bool) -> None:
    """Update chat pane text and autoscroll if at bottom."""
    if chat_text_tag:
        try:
            dpg.set_value(chat_text_tag, chat_text or "")
        except Exception:
            pass
        if chat_was_bottom and chat_scroll_tag:
            try:
                dpg.set_frame_callback(
                    dpg.get_frame_count() + 1,
                    lambda s=None, a=None: dpg.set_y_scroll(chat_scroll_tag, dpg.get_y_scroll_max(chat_scroll_tag))
                )
            except Exception:
                pass

def write_logs(log_text: str, log_scroll_tag: str, log_text_tag: str, log_was_bottom: bool) -> None:
    """Update logs pane text and autoscroll if at bottom."""
    if log_text_tag:
        try:
            dpg.set_value(log_text_tag, log_text or "")
        except Exception:
            pass
        if log_was_bottom and log_scroll_tag:
            try:
                dpg.set_frame_callback(
                    dpg.get_frame_count() + 1,
                    lambda s=None, a=None: dpg.set_y_scroll(log_scroll_tag, dpg.get_y_scroll_max(log_scroll_tag))
                )
            except Exception:
                pass

