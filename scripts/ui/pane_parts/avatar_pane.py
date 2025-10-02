"""Avatar pane — build + resize + fallback drawing (mutually exclusive surfaces)."""
from __future__ import annotations
from ui.layout_constants import L
import dearpygui.dearpygui as dpg

# --- B02 hotfix: ensure avatar image scales correctly after layout settles ---
def post_layout_fix(img_tag=None, container_tag=None):
    """
    After layout settles, size the avatar image to CONTAIN within its panel
    (no over-zoom), preserving aspect ratio. Runs twice to catch late layout.
    Safe no-op if tags are missing.
    """
    try:
        import dearpygui.dearpygui as dpg
    except Exception:
        return
    # ---- self-stabilizing contain-fit ----
    try:
        if not (img_tag and container_tag):
            return

        if not (dpg.does_item_exist(img_tag) and dpg.does_item_exist(container_tag)):
            # Tags not present yet → try again next frame
            dpg.set_frame_callback(dpg.get_frame_count() + 1,
                                   lambda s=None: post_layout_fix(img_tag, container_tag))
            return

        # Get container size (prefer rect size; fallback to configured width/height)
        cw, ch = (0, 0)
        try:
            cw, ch = dpg.get_item_rect_size(container_tag)
        except Exception:
            pass
        if not cw or not ch:
            # fallbacks (may be 0 first frame depending on DPG)
            try: cw = dpg.get_item_width(container_tag)
            except Exception: cw = 0
            try: ch = dpg.get_item_height(container_tag)
            except Exception: ch = 0

        # If container not yet laid out, re-queue and bail
        if not cw or not ch or cw < 8 or ch < 8:
            dpg.set_frame_callback(dpg.get_frame_count() + 1,
                                   lambda s=None,: post_layout_fix(img_tag, container_tag))
            return

        # Original image size (from texture) — width/height the image was created with
        iw = int(dpg.get_item_configuration(img_tag).get("width")  or 0)
        ih = int(dpg.get_item_configuration(img_tag).get("height") or 0)
        if iw <= 0 or ih <= 0:
            # Attempt to read from texture bound to this image
            tex = dpg.get_item_configuration(img_tag).get("texture_tag")
            if tex:
                iw = int(dpg.get_item_configuration(tex).get("width")  or 0) or iw
                ih = int(dpg.get_item_configuration(tex).get("height") or 0) or ih

        if iw <= 0 or ih <= 0:
            # Still unknown — try again next frame
            dpg.set_frame_callback(dpg.get_frame_count() + 1,
                                   lambda s=None: post_layout_fix(img_tag, container_tag))
            return

        # Contain-fit scale with margin (avoid over-zoom); clamp to original size
        margin = getattr(L.AVATAR, "FIT_MARGIN", 0.98)
        scale  = min((cw / iw), (ch / ih)) * float(margin)
        new_w  = int(max(1, min(iw, iw * scale)))
        new_h  = int(max(1, min(ih, ih * scale)))

        # Apply size to image; ensure panel doesn’t show scrollbars
        dpg.configure_item(img_tag, width=new_w, height=new_h)
        try:
            dpg.configure_item(container_tag, no_scrollbar=True)
        except Exception:
            pass

    except Exception:
        # Non-fatal — try again once on the next frame
        try:
            dpg.set_frame_callback(dpg.get_frame_count() + 1,
                                   lambda s=None: post_layout_fix(img_tag, container_tag))
        except Exception:
            pass


