from __future__ import annotations
import dearpygui.dearpygui as dpg

def post_layout_fix(avatar_image_id, parent_container_id, pad: int | None = None) -> None:
    """Recompute avatar image size from its actual parent size (one-time), and reapply wrap/pad."
    """
    try:
        pw, ph = dpg.get_item_rect_size(parent_container_id)
        if not pw or not ph:
            return
        iw = dpg.get_item_width(avatar_image_id) or 1
        ih = dpg.get_item_height(avatar_image_id) or 1
        scale = min(pw / max(iw, 1), ph / max(ih, 1))
        new_w = int(iw * scale)
        new_h = int(ih * scale)
        if new_w > 0 and new_h > 0:
            dpg.configure_item(avatar_image_id, width=new_w, height=new_h)
    except Exception:
        pass

    # keep chat/logs comfy after avatar settles (same logic as legacy)
    try:
        _, h = dpg.get_item_rect_size("chat_scroll")
        dpg.configure_item("chat_pad", height=max(12, int(h * 0.33)))
    except Exception:
        pass
    try:
        _, h = dpg.get_item_rect_size("logs_scroll")
        dpg.configure_item("logs_pad", height=max(12, int(h * 0.33)))
    except Exception:
        pass

    try:
        w,_ = dpg.get_item_rect_size("chat_scroll")
        dpg.configure_item("chat_text", wrap=max(0, int(w - (pad or 0))))
    except Exception:
        pass
    try:
        w,_ = dpg.get_item_rect_size("logs_scroll")
        dpg.configure_item("log_text", wrap=max(0, int(w - (pad or 0))))
    except Exception:
        pass

