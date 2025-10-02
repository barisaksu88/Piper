# -*- coding: utf-8 -*-
"""
B04.22g â€” Consolidate avatar sizing into layout constants; rewire callers; fix pane_parts __init__.
- Adds/ensures L.AVATAR and L.RIGHTCOL constants.
- Rewrites panes.py to reference constants (no hardcoded 180/400/408).
- Rewrites avatar_pane.py to use FIT_MARGIN from constants.
- Fixes scripts/ui/pane_parts/__init__.py to avoid importing non-existent modules.
"""
from __future__ import annotations
from pathlib import Path
import re

ROOT = Path(r"C:\Piper\scripts")
LAYOUT = ROOT / "ui" / "layout_constants.py"
PANES  = ROOT / "ui" / "panes.py"
AVATAR = ROOT / "ui" / "pane_parts" / "avatar_pane.py"
PPINIT = ROOT / "ui" / "pane_parts" / "__init__.py"

def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write(p: Path, s: str):
    p.write_text(s, encoding="utf-8")
    print(f"[B04.22g] updated: {p}")

def ensure_layout_constants():
    text = read(LAYOUT)
    orig = text

    # Ensure AVATAR section with constants (multiplicative FIT_MARGIN stays 0.98 to match prior logic)
    if "class AVATAR" not in text:
        block = (
            "\n\nclass AVATAR:\n"
            "    # Height of the avatar panel (right/bottom) in px\n"
            "    HEIGHT = 408\n"
            "    # Minimal initial avatar panel height for cold build (pre-calibration)\n"
            "    MIN_HEIGHT = 180\n"
            "    # Contain-fit margin multiplier to avoid jitter (0.98 = -2%)\n"
            "    FIT_MARGIN = 0.98\n"
        )
        text += block
    else:
        # add missing fields if any; do NOT overwrite existing values
        def upsert(name, val):
            nonlocal text
            if not re.search(rf"^\s*{name}\s*=", text, re.MULTILINE):
                text = re.sub(r"(class AVATAR:)", rf"\1\n    {name} = {val}", text, count=1)
        upsert("HEIGHT", "408")
        upsert("MIN_HEIGHT", "180")
        upsert("FIT_MARGIN", "0.98")

    # Ensure RIGHTCOL reserved height (logs-over-avatar layout)
    if "class RIGHTCOL" not in text:
        # Need reference to L.SPACE.SECTION_GAP but we can compute at use sites; still define for clarity.
        block = (
            "\n\nclass RIGHTCOL:\n"
            "    # Space reserved below logs for avatar panel + vertical gap\n"
            "    # NOTE: At use sites we compute ROW_H - (AVATAR.HEIGHT + L.SPACE.SECTION_GAP)\n"
            "    # Kept as semantic anchor for future layout changes.\n"
            "    RESERVED_BELOW = None  # computed at call sites to avoid circular imports\n"
        )
        text += block

    if text != orig:
        write(LAYOUT, text)
    else:
        print("[B04.22g] layout_constants.py already has needed sections.")

def rewire_panes():
    text = read(PANES)
    orig = text

    # Ensure import of L (idempotent)
    if "from scripts.ui.layout_constants import L" not in text:
        text = re.sub(
            r"(^import .+?\n)",
            r"\1from scripts.ui.layout_constants import L\n",
            text,
            count=1,
            flags=re.MULTILINE
        )

    # child_window(... tag="avatar_panel", height=180 -> MIN_HEIGHT)
    text = re.sub(
        r'(child_window\([^)]*tag\s*=\s*"avatar_panel"[^)]*?height\s*=\s*)180',
        r'\1L.AVATAR.MIN_HEIGHT',
        text,
        flags=re.DOTALL
    )

    # Any configure_item("avatar_panel", height=400/408) -> HEIGHT
    text = re.sub(
        r'configure_item\(\s*"avatar_panel"\s*,\s*height\s*=\s*(400|408)\s*\)',
        r'configure_item("avatar_panel", height=L.AVATAR.HEIGHT)',
        text
    )

    # Logs height: (ROW_H - 400 - L.SPACE.SECTION_GAP) or (ROW_H - 408 - L.SPACE.SECTION_GAP) -> (ROW_H - L.AVATAR.HEIGHT - L.SPACE.SECTION_GAP)
    text = re.sub(
        r'height=\(\s*ROW_H\s*-\s*(400|408)\s*-\s*L\.SPACE\.SECTION_GAP\s*\)',
        r'height=(ROW_H - L.AVATAR.HEIGHT - L.SPACE.SECTION_GAP)',
        text
    )

    if text != orig:
        write(PANES, text)
    else:
        print("[B04.22g] panes.py already rewired.")

def rewire_avatar_pane():
    if not AVATAR.exists():
        print("[B04.22g] avatar_pane.py not found; skipping.")
        return
    text = read(AVATAR)
    orig = text

    # Ensure import of L
    if "from scripts.ui.layout_constants import L" not in text:
        text = re.sub(
            r"(^from __future__ import annotations\s*[\r\n]+)",
            r"\1from scripts.ui.layout_constants import L\n",
            text,
            count=1,
            flags=re.MULTILINE
        )

    # Replace multiplicative margins "* 0.98" with "* L.AVATAR.FIT_MARGIN"
    text = re.sub(r"\*\s*0\.98\b", r"* L.AVATAR.FIT_MARGIN", text)

    if text != orig:
        write(AVATAR, text)
    else:
        print("[B04.22g] avatar_pane.py already uses constants.")

def fix_pane_parts_init():
    # __init__.py should NOT import a non-existent 'panes' module.
    content = (
        '# pane_parts package exports\n'
        'from .avatar_pane import post_layout_fix as avatar_post_layout_fix\n'
        'try:\n'
        '    from .avatar_pane import resize as avatar_resize\n'
        'except Exception:\n'
        '    pass\n'
    )
    write(PPINIT, content)

def main():
    ensure_layout_constants()
    rewire_panes()
    rewire_avatar_pane()
    fix_pane_parts_init()

if __name__ == "__main__":
    main()

