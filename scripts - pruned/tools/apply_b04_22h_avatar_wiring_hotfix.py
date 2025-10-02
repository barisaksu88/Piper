# -*- coding: utf-8 -*-
"""
B04.22h â€” Wire AVATAR/RIGHTCOL onto L; make pane_parts init lazy.
"""

from __future__ import annotations
from pathlib import Path
import re

ROOT = Path(r"C:\Piper\scripts")
LAYOUT = ROOT / "ui" / "layout_constants.py"
PPINIT = ROOT / "ui" / "pane_parts" / "__init__.py"

def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")
    print(f"[B04.22h] updated: {p}")

def patch_layout_constants():
    text = read(LAYOUT)
    orig = text

    # Ensure there is an 'L' container; we won't create one, we just attach to it if present.
    # Append wiring lines at end so that AVATAR/RIGHTCOL are attached to L.
    wiring = (
        "\n# --- B04.22h: attach AVATAR/RIGHTCOL onto L if not already present ---\n"
        "try:\n"
        "    _ = L.AVATAR  # type: ignore[attr-defined]\n"
        "except Exception:\n"
        "    try:\n"
        "        L.AVATAR = AVATAR  # type: ignore\n"
        "    except Exception:\n"
        "        pass\n"
        "try:\n"
        "    _ = L.RIGHTCOL  # type: ignore[attr-defined]\n"
        "except Exception:\n"
        "    try:\n"
        "        L.RIGHTCOL = RIGHTCOL  # type: ignore\n"
        "    except Exception:\n"
        "        pass\n"
    )

    # Only add once
    if "B04.22h: attach AVATAR/RIGHTCOL" not in text:
        text = text.rstrip() + "\n" + wiring

    if text != orig:
        write(LAYOUT, text)
    else:
        print("[B04.22h] layout_constants.py already wired.")

def patch_pane_parts_init():
    # Make init lazy; avoid importing non-existent modules or heavy imports at package import time.
    content = (
        "# pane_parts package â€” lazy exports (B04.22h)\n"
        "def avatar_post_layout_fix(*args, **kwargs):\n"
        "    from .avatar_pane import post_layout_fix\n"
        "    return post_layout_fix(*args, **kwargs)\n"
        "\n"
        "def avatar_resize(*args, **kwargs):\n"
        "    try:\n"
        "        from .avatar_pane import resize\n"
        "    except Exception:\n"
        "        return None\n"
        "    return resize(*args, **kwargs)\n"
    )
    write(PPINIT, content)

def main():
    patch_layout_constants()
    patch_pane_parts_init()

if __name__ == \"__main__\":
    main()

