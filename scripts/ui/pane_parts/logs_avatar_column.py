"""Right column: Logs + Avatar panel (lean, no fallbacks)."""
from __future__ import annotations
import os
import dearpygui.dearpygui as dpg
from ui.layout_constants import L
from ui.pane_parts.avatar_pane import post_layout_fix as _avatar_post_layout_fix

_AVATAR_TEX = None
_AVATAR_IMG_W = 0
_AVATAR_IMG_H = 0


def _ensure_texture_registry():
    if not dpg.does_item_exist("texture_registry"):
        with dpg.texture_registry(tag="texture_registry"):
            pass


def _load_avatar_from_file(path: str):
    global _AVATAR_TEX, _AVATAR_IMG_W, _AVATAR_IMG_H
    if not path or not os.path.exists(path):
        return None
    _ensure_texture_registry()
    w, h, c, data = dpg.load_image(path)
    tex_id = dpg.add_static_texture(w, h, data, parent="texture_registry")
    _AVATAR_TEX, _AVATAR_IMG_W, _AVATAR_IMG_H = tex_id, w, h
    return tex_id


def build(*, parent: str, pane_theme, log_path: str) -> None:
    RIGHT_W = L.PANE.RIGHT_WIDTH
    PAD = L.SPACE.PAD
    ROW_H = L.WINDOW.HEIGHT - L.PANE.BODY_VPAD

    if dpg.does_item_exist("logs_container"):
        return

    with dpg.group(parent=parent, tag="logs_container"):
        # Logs header row
        with dpg.group(horizontal=True):
            dpg.add_text("Logs", tag="logs_hdr")
            dpg.add_spacer(width=L.SPACE.GAP)
            dpg.add_button(label="Copy Logs", tag="copy_logs_btn",
                           callback=lambda: dpg.set_clipboard_text(dpg.get_value("log_text") or ""))
            dpg.add_spacer(width=L.SPACE.GAP)

        dpg.add_spacer(height=L.CHAT.INSET_TOP)

        # Logs scroll area
        with dpg.child_window(
            tag="logs_scroll",
            width=(RIGHT_W - PAD),
            height=(ROW_H - L.AVATAR.PANEL_H - L.SPACE.SECTION_GAP),
            autosize_x=False,
            autosize_y=False,
            no_scrollbar=False,
        ):
            dpg.add_text("", tag="log_text", wrap=(RIGHT_W - PAD))
            dpg.add_spacer(tag="logs_pad", height=L.SPACE.SMALL)
        dpg.bind_item_theme("logs_scroll", pane_theme)

        # Avatar panel
        with dpg.child_window(
            tag="avatar_panel",
            width=(RIGHT_W - PAD),
            height=L.AVATAR.PANEL_H,
            autosize_x=False,
            autosize_y=False,
            no_scrollbar=True,
        ):
            dpg.bind_item_theme("avatar_panel", pane_theme)
            if not dpg.does_item_exist("avatar_draw"):
                dpg.add_drawlist(width=(RIGHT_W - PAD), height=L.AVATAR.PANEL_H, tag="avatar_draw")

        # Load a placeholder avatar image
        cwd = os.getcwd()
        env_avatar = os.environ.get("PIPER_AVATAR", "").strip()
        candidates = [
            env_avatar if env_avatar else None,
            os.path.join(cwd, "assets", "avatar.png"),
            os.path.join(cwd, "Library", "Confident Scientist in the Lab.png"),
            os.path.join(cwd, "Library", "avatar.png"),
        ]
        chosen = next((p for p in candidates if p and os.path.exists(p)), None)
        if chosen:
            _ensure_texture_registry()
            _load_avatar_from_file(chosen)
            if _AVATAR_TEX:
                if not dpg.does_item_exist("avatar_image"):
                    dpg.add_image(_AVATAR_TEX, tag="avatar_image", parent="avatar_panel",
                                  width=(RIGHT_W - PAD - L.SPACE.MID), height=(L.AVATAR.PANEL_H - L.SPACE.MID))
                if dpg.does_item_exist("avatar_draw"):
                    dpg.configure_item("avatar_draw", show=False)

        # Align and fit avatar
        dpg.configure_item("avatar_panel", height=L.AVATAR.PANEL_H)
        dpg.set_frame_callback(dpg.get_frame_count() + 1,
                               lambda s=None: _avatar_post_layout_fix("avatar_image", "avatar_panel"))

        def _recalibrate_avatar_on_resize(user_data=None):
            dpg.set_frame_callback(
                dpg.get_frame_count() + 2,
                lambda s=None: _avatar_post_layout_fix("avatar_image", "avatar_panel")
            )
        dpg.set_viewport_resize_callback(_recalibrate_avatar_on_resize)
