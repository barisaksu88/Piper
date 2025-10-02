"""
B04.2 Scroll utilities — extracted from ui/panes.py
No new literals; pure helpers.
"""
import dearpygui.dearpygui as dpg
from typing import Iterable, Union, Optional

__all__ = [
    "is_at_bottom",
    "scroll_to_bottom_next_frame",
    "update_autoscroll_badges",
    # breathing room helpers
    "set_bottom_padding",
    "set_bottom_padding_next_frame",
    # convenience combo
    "apply_autoscroll_and_breathing",
]

def is_at_bottom(tag: str, threshold: int = 48) -> bool:
    """Return True if scrollable region is near bottom within threshold px."""
    if not tag:
        return False
    try:
        cur = dpg.get_y_scroll(tag)
        mx = dpg.get_y_scroll_max(tag)
        return (mx - cur) <= threshold
    except Exception:
        return False

def scroll_to_bottom_next_frame(tag_or_tags: Union[Optional[str], Iterable[Optional[str]]], *, retries: int = 2):
    """Scroll one or more containers to bottom now and in the next frames.
    More reliable when layout updates after text changes.
    """
    try:
        # normalize to a flat list
        if isinstance(tag_or_tags, (list, tuple, set)):
            tags = [t for t in tag_or_tags if t]
        else:
            tags = [tag_or_tags] if tag_or_tags else []
        if not tags:
            return

        def _bump(_tries_left: int = retries):
            for t in tags:
                try:
                    mx = dpg.get_y_scroll_max(t)
                    dpg.set_y_scroll(t, mx if (mx and mx > 0) else 1_000_000)
                except Exception:
                    pass
            if _tries_left > 0:
                try:
                    dpg.set_frame_callback(dpg.get_frame_count() + 1, lambda s=None: _bump(_tries_left - 1))
                except Exception:
                    pass

        # do one immediate bump plus scheduled retries
        _bump(retries)
    except Exception:
        pass


def update_autoscroll_badges(chat_scroll_tag, chat_badge_tag, log_scroll_tag, log_badge_tag) -> None:
    """
    Show/hide tiny 'autoscroll' badges based on whether each pane is at bottom.
    Safe no-op if tags are missing.
    """
    try:
        if chat_scroll_tag and chat_badge_tag:
            dpg.configure_item(chat_badge_tag, show=not is_at_bottom(chat_scroll_tag))
    except Exception:
        pass
    try:
        if log_scroll_tag and log_badge_tag:
            dpg.configure_item(log_badge_tag, show=not is_at_bottom(log_scroll_tag))
    except Exception:
        pass

# --- Breathing room helpers -----------------------------------------------

def set_bottom_padding(spacer_tag: Optional[str], height: int) -> None:
    """Set bottom spacer height (breathing room) if the spacer exists."""
    if not spacer_tag:
        return
    try:
        if dpg.does_item_exist(spacer_tag):
            dpg.configure_item(spacer_tag, height=max(0, int(height)))
    except Exception:
        pass


def set_bottom_padding_next_frame(spacer_tag: Optional[str], height: int) -> None:
    """Schedule breathing-room padding update on the next frame."""
    try:
        dpg.set_frame_callback(
            dpg.get_frame_count() + 1,
            lambda s=None: set_bottom_padding(spacer_tag, height),
        )
    except Exception:
        pass


# --- Convenience combo -----------------------------------------------------

def apply_autoscroll_and_breathing(
    scroll_tag: Optional[str],
    spacer_tag: Optional[str],
    spacer_height: int,
) -> None:
    """On next frame: scroll to bottom and apply breathing-room padding."""
    try:
        scroll_to_bottom_next_frame(scroll_tag)
        set_bottom_padding_next_frame(spacer_tag, spacer_height)
    except Exception:
        pass
