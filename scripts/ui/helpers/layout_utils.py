"""
B04.12b Layout helpers â€” extracted from ui/panes.py (wrap/spacing nits).
No layout constants added; pure DearPyGui wrapper utilities.
"""
from __future__ import annotations
import dearpygui.dearpygui as dpg

def apply_wraps_if_present(tag_candidates, wrap: int) -> None:
    """
    Set text wrap width on the first existing tag from tag_candidates.
    Safe no-op if nothing exists or item is not a text widget.
    """
    try:
        for t in tag_candidates:
            if dpg.does_item_exist(t):
                try:
                    dpg.configure_item(t, wrap=wrap)
                except Exception:
                    pass
                return
    except Exception:
        pass

def update_bottom_padding_if_present(container_candidates, pad: int) -> None:
    """
    Ensure there is a bottom padding spacer under the first existing container.
    - If found (any alias), resize it.
    - If missing, create a spacer using the LEGACY tag names so existing code sees it:
      chat  -> "chat_pad"
      logs  -> "logs_pad"
      other -> "bottom_pad"
    - Always move it to the end so it remains the last child (keeps a visible gap).
    Recognized aliases (read/resize): "chat_pad", "logs_pad", "bottom_pad",
                                      "chat_bottom_pad", "logs_bottom_pad"
    """
    try:
        parent = None
        first_name = None
        for c in container_candidates:
            if dpg.does_item_exist(c):
                parent = c
                first_name = str(c).lower()
                break
        if not parent:
            return

        # Which tag should we prefer when creating?
        if first_name and "chat" in first_name:
            create_tag = "chat_pad"
        elif first_name and ("log" in first_name or "logs" in first_name):
            create_tag = "logs_pad"
        else:
            create_tag = "bottom_pad"

        # Known aliases we will accept/resize if present
        aliases = ("chat_pad", "logs_pad", "bottom_pad",
                   "chat_bottom_pad", "logs_bottom_pad")

        # Try to find an existing spacer by alias
        found = None
        children = dpg.get_item_children(parent, 1) or []
        for child in children:
            try:
                alias = dpg.get_item_alias(child)
            except Exception:
                alias = None
            if alias in aliases:
                found = alias
                break

        tag = found or create_tag

        # Create if missing
        if not dpg.does_item_exist(tag):
            try:
                dpg.add_spacer(parent=parent, tag=tag, height=pad)
            except Exception:
                # fallback anonymous spacer if tag collides
                try:
                    dpg.add_spacer(parent=parent, height=pad)
                except Exception:
                    return
        else:
            try:
                dpg.configure_item(tag, height=pad)
            except Exception:
                pass

        # Ensure spacer stays last in the scroll region
        try:
            dpg.move_item(tag, parent=parent)
        except Exception:
            try:
                children = dpg.get_item_children(parent, 1) or []
                if children:
                    dpg.move_item(children[-1], parent=parent)
            except Exception:
                pass

    except Exception:
        pass

