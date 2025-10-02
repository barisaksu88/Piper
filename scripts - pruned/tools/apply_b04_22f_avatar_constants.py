# -*- coding: utf-8 -*-
"""
B04.22f â€” Consolidate avatar sizing: layout constants + callers.
- Adds L.AVATAR.{PANEL_H, MIN_PANEL_H, FIT_MARGIN} to ui/layout_constants.py (idempotent).
- Replaces hardcoded avatar heights (180, 400/408) in ui/panes.py with L.AVATAR constants.
- Replaces contain-fit margin 0.98 in pane_parts/avatar_pane.py with L.AVATAR.FIT_MARGIN.
No new literals outside layout_constants.py; respects Piper invariants.
"""
from __future__ import annotations
from pathlib import Path
import re

ROOT = Path(r"C:\Piper\scripts")
LAYOUT = ROOT / "ui" / "layout_constants.py"
PANES  = ROOT / "ui" / "panes.py"
AVATAR = ROOT / "ui" / "pane_parts" / "avatar_pane.py"

def ensure_constants():
    text = LAYOUT.read_text(encoding="utf-8")
    orig = text

    # Ensure import/export shape for L and add AVATAR section if missing.
    if "class AVATAR" not in text:
        # Insert AVATAR section near the end of file, before final export of L if present.
        block = (
            "\n\nclass AVATAR:\n"
            "    # Height of the avatar panel (right/bottom) in pixels.\n"
            "    PANEL_H = 408\n"
            "    # Minimal initial avatar panel height for cold build (pre-calibration).\n"
            "    MIN_PANEL_H = 180\n"
            "    # Contain-fit margin to avoid scrollbar jitter in Dear PyGui.\n"
            "    FIT_MARGIN = 0.98\n"
        )
        # Try to insert before a trailing "class L:" or after, depending on file shape
        if "class L" in text:
            # Place AVATAR block above the 'class L' or extend if L aggregates components.
            text = re.sub(r"(\nclass L\b)", block + r"\n\1", text, count=1)
            if text == orig:  # fall back to appending at end
                text = orig + block
        else:
            text = orig + block

    else:
        # Ensure fields exist / correct (idempotent upserts)
        def upsert(name, default):
            nonlocal text
            pat = re.compile(rf"(class AVATAR:[\s\S]*?)(^\s*{name}\s*=\s*.+?$)", re.MULTILINE)
            if re.search(rf"^\s*{name}\s*=", text, re.MULTILINE):
                # leave existing value as-is
                return
            text = re.sub(r"(class AVATAR:)", rf"\1\n    {name} = {default}", text, count=1)
        upsert("PANEL_H", "408")
        upsert("MIN_PANEL_H", "180")
        upsert("FIT_MARGIN", "0.98")

    if text != orig:
        LAYOUT.write_text(text, encoding="utf-8")
        print("[B04.22f] layout_constants.py: AVATAR constants ensured.")
    else:
        print("[B04.22f] layout_constants.py: no changes needed.")

def rewire_panes():
    text = PANES.read_text(encoding="utf-8")
    orig = text

    # Ensure L is imported
    if "layout_constants import L" not in text:
        text = text.replace(
            "from scripts.ui.layout_constants import",
            "from scripts.ui.layout_constants import",  # keep existing
        )
        if "from scripts.ui.layout_constants import L" not in text:
            # Add a dedicated import at top after other imports
            text = re.sub(r"(^from .+?$)", r"\1", text, flags=re.MULTILINE)
            if "from scripts.ui.layout_constants import L" not in text:
                text = re.sub(r"(\n)(import .+?\n)", r"\1\2from scripts.ui.layout_constants import L\n", text, count=1)

    # Replace logs height formula `(ROW_H - 400 - L.SPACE.SECTION_GAP)` or `(ROW_H - 408 - ...)`
    text = re.sub(
        r"height=\(\s*ROW_H\s*-\s*(400|408)\s*-\s*L\.SPACE\.SECTION_GAP\s*\)",
        r"height=(ROW_H - L.AVATAR.PANEL_H - L.SPACE.SECTION_GAP)",
        text,
    )
    # Replace avatar_panel fixed configure/creation heights 180, 400, 408 with constants
    text = re.sub(
        r'configure_item\(\s*"avatar_panel"\s*,\s*height\s*=\s*(400|408)\s*\)',
        r'configure_item("avatar_panel", height=L.AVATAR.PANEL_H)',
        text,
    )
    text = re.sub(
        r'child_window\(\s*[^)]*tag\s*=\s*"avatar_panel"[^)]*height\s*=\s*180',
        lambda m: m.group(0).replace("height=180", "height=L.AVATAR.MIN_PANEL_H"),
        text,
        flags=re.DOTALL
    )

    if text != orig:
        PANES.write_text(text, encoding="utf-8")
        print("[B04.22f] panes.py: rewired heights to L.AVATAR constants.")
    else:
        print("[B04.22f] panes.py: no changes needed.")

def rewire_avatar_fit():
    if not AVATAR.exists():
        print("[B04.22f] avatar_pane.py not found; skipping fit-margin rewire.")
        return
    text = AVATAR.read_text(encoding="utf-8")
    orig = text

    # Ensure L import
    if "layout_constants import L" not in text:
        # insert import near top after other imports
        text = re.sub(
            r"(^from __future__ import annotations\s*[\r\n]+)",
            r"\1from scripts.ui.layout_constants import L\n",
            text,
            count=1,
            flags=re.MULTILINE
        )

    # Replace '* 0.98' with '* L.AVATAR.FIT_MARGIN'
    text = re.sub(r"\*\s*0\.98\b", r"* L.AVATAR.FIT_MARGIN", text)

    if text != orig:
        AVATAR.write_text(text, encoding="utf-8")
        print("[B04.22f] avatar_pane.py: rewired fit margin to L.AVATAR.FIT_MARGIN.")
    else:
        print("[B04.22f] avatar_pane.py: no changes needed.")

def main():
    ensure_constants()
    rewire_panes()
    rewire_avatar_fit()

if __name__ == "__main__":
    main()

