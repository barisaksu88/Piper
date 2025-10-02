# -*- coding: utf-8 -*-
"""
B04.13a â€” peel legacy avatar post-layout fix out of ui/panes.py
- Create scripts/ui/helpers/avatar_fix.py with post_layout_fix()
- Replace local _avatar_post_layout_fix(...) definition in panes.py
- Update call sites to use helper
Safe, idempotent; logs what changed.
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path("C:/Piper/scripts").resolve()
PANES = ROOT / "ui" / "panes.py"
HELPERS_DIR = ROOT / "ui" / "helpers"
AVATAR_FIX = HELPERS_DIR / "avatar_fix.py"

AVATAR_HELPER_BODY = """\
from __future__ import annotations
import dearpygui.dearpygui as dpg

def post_layout_fix(avatar_image_id, parent_container_id, pad: int | None = None) -> None:
    \"\"\"Recompute avatar image size from its actual parent size (one-time), and reapply wrap/pad.\"
    \"\"\"
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
"""

def ensure_helper():
    HELPERS_DIR.mkdir(parents=True, exist_ok=True)
    if not AVATAR_FIX.exists():
        AVATAR_FIX.write_text(AVATAR_HELPER_BODY, encoding="utf-8")
        print(f"[B04.13a] Created: {AVATAR_FIX}")
    else:
        print(f"[B04.13a] Exists:  {AVATAR_FIX}")

def patch_panes():
    text = PANES.read_text(encoding="utf-8")
    orig = text

    # 1) add import if missing
    import_line = "from scripts.ui.helpers.avatar_fix import post_layout_fix as _avatar_post_layout_fix  # B04.13a"
    if "helpers.avatar_fix import post_layout_fix" not in text:
        lines = text.splitlines()
        insert_idx = 0
        for i, line in enumerate(lines[:150]):
            if line.startswith("from scripts.ui.helpers"):
                insert_idx = i + 1
        lines.insert(insert_idx, import_line)
        text = "\n".join(lines)
        print("[B04.13a] Added avatar_fix import")

    # 2) remove the local def _avatar_post_layout_fix(...) block if present
    def_pat = re.compile(
        r"def\s+_avatar_post_layout_fix\s*\([^)]*\):\s*\n(?:\s+.*\n)+?\n(?=\s*def|\s*#|\s*$)",
        re.MULTILINE
    )
    text, n = def_pat.subn("", text)
    if n:
        print(f"[B04.13a] Removed local _avatar_post_layout_fix (x{n})")

    # 3) keep existing call sites intact â€” they already call _avatar_post_layout_fix(...)
    if text != orig:
        PANES.write_text(text, encoding="utf-8")
        print(f"[B04.13a] Patched: {PANES}")
    else:
        print("[B04.13a] No changes needed (already clean).")

def main():
    ensure_helper()
    patch_panes()

if __name__ == "__main__":
    main()

