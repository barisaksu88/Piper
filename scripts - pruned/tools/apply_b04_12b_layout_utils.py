# -*- coding: utf-8 -*-
"""
B04.12b â€” layout helpers peel:
- Create scripts/ui/helpers/layout_utils.py with apply_wraps_if_present + update_bottom_padding_if_present
- Update scripts/ui/panes.py to import & use these helpers
- Replace any _apply_wraps/_update_bottom_padding calls
- Insert helper calls after tag resolver if theyâ€™re missing
- Remove old local helper defs if present
Safe, idempotent, logs what it changed.
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path("C:/Piper/scripts").resolve()
PANES = ROOT / "ui" / "panes.py"
HELPERS_DIR = ROOT / "ui" / "helpers"
LAYOUT_UTILS = HELPERS_DIR / "layout_utils.py"

LAYOUT_UTILS_BODY = """\
\"\"\"
B04.12b Layout helpers â€” extracted from ui/panes.py (wrap/spacing nits).
No layout constants added; pure DearPyGui wrapper utilities.
\"\"\"
from __future__ import annotations
import dearpygui.dearpygui as dpg

def apply_wraps_if_present(tag_candidates, wrap: int) -> None:
    \"\"\"Set text wrap width on the first existing tag from tag_candidates.\"\"\"
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
    \"\"\"Adjust a bottom padding spacer if present under the first existing container.\"\"\"
    try:
        parent = None
        for c in container_candidates:
            if dpg.does_item_exist(c):
                parent = c
                break
        if not parent:
            return
        children = dpg.get_item_children(parent, 1) or []
        for child in children:
            try:
                alias = dpg.get_item_alias(child)
            except Exception:
                alias = None
            if alias in ("bottom_pad", "chat_bottom_pad", "logs_bottom_pad"):
                try:
                    dpg.configure_item(child, height=pad)
                except Exception:
                    pass
                return
    except Exception:
        pass
"""

def ensure_layout_utils():
    HELPERS_DIR.mkdir(parents=True, exist_ok=True)
    if not LAYOUT_UTILS.exists():
        LAYOUT_UTILS.write_text(LAYOUT_UTILS_BODY, encoding="utf-8")
        print(f"[B04.12b] Created: {LAYOUT_UTILS}")
    else:
        print(f"[B04.12b] Exists:  {LAYOUT_UTILS}")

def patch_panes():
    src = PANES.read_text(encoding="utf-8")
    orig = src

    # 1) Ensure import present
    import_line = "from scripts.ui.helpers.layout_utils import apply_wraps_if_present, update_bottom_padding_if_present  # B04.12b"
    if "helpers.layout_utils import apply_wraps_if_present" not in src:
        lines = src.splitlines()
        insert_idx = 0
        for i, line in enumerate(lines[:150]):
            if line.startswith("from scripts.ui.helpers"):
                insert_idx = i + 1
        lines.insert(insert_idx, import_line)
        src = "\n".join(lines)
        print("[B04.12b] Added layout_utils import")

    # 2) Replace calls to local helpers with new helpers
    repls = [
        (r"_apply_wraps\(\s*\[([^\]]*)\]\s*,\s*CHAT_WRAP\s*\)", r"apply_wraps_if_present([\[\1\]], CHAT_WRAP)"),
        (r"_apply_wraps\(\s*\[([^\]]*)\]\s*,\s*LOG_WRAP\s*\)",  r"apply_wraps_if_present([\[\1\]], LOG_WRAP)"),
        (r"_update_bottom_padding\(\s*\[([^\]]*)\]\s*,\s*CHAT_BOTTOM_PAD\s*\)",
         r"update_bottom_padding_if_present([\[\1\]], CHAT_BOTTOM_PAD)"),
        (r"_update_bottom_padding\(\s*\[([^\]]*)\]\s*,\s*LOG_BOTTOM_PAD\s*\)",
         r"update_bottom_padding_if_present([\[\1\]], LOG_BOTTOM_PAD)"),
    ]
    for pat, rep in repls:
        src, n = re.subn(pat, rep, src)
        if n:
            print(f"[B04.12b] Replaced {n} occurrence(s) of pattern: {pat}")

    # 3) If helper calls are missing, insert them after tag resolver lines
    if ("apply_wraps_if_present(" not in src) or ("update_bottom_padding_if_present(" not in src):
        tag_anchor = re.search(
            r"CHAT_SCROLL\s*=.*\n.*CHAT_TEXT\s*=.*\n.*LOG_SCROLL\s*=.*\n.*LOG_TEXT\s*=.*",
            src
        )
        if tag_anchor:
            inject = []
            if "apply_wraps_if_present(" not in src:
                inject.append('    apply_wraps_if_present(["chat_text", "chat_buffer"], CHAT_WRAP)')
                inject.append('    apply_wraps_if_present(["log_text", "logs_text", "log_buffer"], LOG_WRAP)')
            if "update_bottom_padding_if_present(" not in src:
                inject.append('    update_bottom_padding_if_present(["chat_scroll", "chat_view", "chat_region"], CHAT_BOTTOM_PAD)')
                inject.append('    update_bottom_padding_if_present(["log_scroll", "logs_scroll", "log_view", "logs_view"], LOG_BOTTOM_PAD)')
            src = src[:tag_anchor.end()] + "\n" + "\n".join(inject) + "\n" + src[tag_anchor.end():]
            print("[B04.12b] Inserted helper calls after tag resolver")

    # 4) Remove local helper definitions if present
    def drop_local(name):
        nonlocal src
        pat = rf"def\s+{name}\s*\([^)]*\):\s*\n(?:\s+.*\n)+?"
        m = re.search(pat, src)
        if m and ("wrap" in m.group(0) or "padding" in m.group(0)):
            src = src[:m.start()] + src[m.end():]
            print(f"[B04.12b] Removed local helper: {name}()")

    drop_local("_apply_wraps")
    drop_local("_update_bottom_padding")

    if src != orig:
        PANES.write_text(src, encoding="utf-8")
        print(f"[B04.12b] Patched: {PANES}")
    else:
        print("[B04.12b] No changes needed (already clean).")

def main():
    ensure_layout_utils()
    patch_panes()

if __name__ == "__main__":
    main()

