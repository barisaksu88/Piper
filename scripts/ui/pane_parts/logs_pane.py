"""Logs pane — build + refresh hooks."""
from __future__ import annotations
import dearpygui.dearpygui as dpg

def build() -> None:
    """
    Create the logs widgets if they do not exist.
    Expected parent: group 'body_row'.
    Tags created here:
      logs_container, log_autoscroll_badge, logs_scroll, log_text, logs_pad
    """
    try:
        if not dpg.does_item_exist("body_row"):
            return
        if dpg.does_item_exist("logs_container"):
            return

        with dpg.group(parent="body_row", tag="logs_container"):
            with dpg.group(horizontal=True):
                dpg.add_text("Logs")
                dpg.add_spacer(width=10)
                dpg.add_button(label="Copy Logs", callback=lambda: None)  # Compatibility shim for older panes imports. Do NOT use in new code. Scheduled for removal post-Phase B04.
                dpg.add_spacer(width=10)
                dpg.add_text("[⏸ autoscroll]", tag="log_autoscroll_badge", show=False)
            dpg.add_separator()
            dpg.add_spacer(height=6)

            with dpg.child_window(
                tag="logs_scroll",
                autosize_x=True,
                autosize_y=False,
                no_scrollbar=False,
            ):
                dpg.add_text("", tag="log_text")
                dpg.add_spacer(tag="logs_pad", height=8)
    except Exception:
        pass


def refresh(text: str, autoscroll_on: bool) -> None:
    """Update logs text and follow if user was at bottom."""
    try:
        if dpg.does_item_exist("log_text"):
            dpg.set_value("log_text", text)
        if autoscroll_on and dpg.does_item_exist("logs_scroll"):
            try:
                dpg.set_y_scroll("logs_scroll", dpg.get_y_scroll_max("logs_scroll"))
            except Exception:
                pass
    except Exception:
        pass

