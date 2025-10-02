# -*- coding: utf-8 -*-
"""
B04.13b â€” extract refresh_ui(...) from ui/panes.py into ui/helpers/refresh_core.py
- Creates/updates scripts/ui/helpers/refresh_core.py with the exact function body
- Replaces the function in panes.py with an import shim
- Idempotent: safe to re-run
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path("C:/Piper/scripts").resolve()
PANES = ROOT / "ui" / "panes.py"
HELPERS_DIR = ROOT / "ui" / "helpers"
REFRESH_CORE = HELPERS_DIR / "refresh_core.py"

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

def extract_refresh(src: str):
    """
    Returns (pre, func, post) split around def refresh_ui(...): ... end
    Uses a conservative regex: def refresh_ui( ... ) -> None:\n <indented body>
    """
    # Match the whole def block by indentation (assumes 4 spaces indent in body)
    pat = re.compile(
        r'(^def\s+refresh_ui\s*\([^)]*\)\s*->\s*None\s*:\s*\n'
        r'(?:^(?: {4}|\t).*\n)+)',                              # indented body
        re.MULTILINE
    )
    m = pat.search(src)
    if not m:
        pat2 = re.compile(
            r'(^def\s+refresh_ui\s*\([^)]*\)\s*:\s*\n'
            r'(?:^(?: {4}|\t).*\n)+)',
            re.MULTILINE
        )
        m = pat2.search(src)
        if not m:
            return None, None, None
    start, end = m.span(1)
    pre = src[:start]
    func = src[start:end]
    post = src[end:]
    return pre, func, post

def ensure_helper_file(func_block: str):
    """Create/replace refresh_core.py with the function block and a thin header."""
    header = (
        '"""B04.13b refresh core â€” extracted from ui/panes.py (behavior-preserving)."""\n'
        'from __future__ import annotations\n'
        '# NOTE: imports remain in panes.py; this module only hosts refresh_ui.\n\n'
    )
    # Keep only the function block; no extra imports to avoid duplication.
    content = header + func_block
    write_text(REFRESH_CORE, content)

def patch_panes(pre: str, post: str) -> str:
    """Insert import shim into panes.py and return new source."""
    lines = pre.splitlines()
    insert_idx = 0
    # place the import after other helper imports
    for i, line in enumerate(lines[:200]):
        if line.startswith("from scripts.ui.helpers"):
            insert_idx = i + 1
    shim = "from scripts.ui.helpers.refresh_core import refresh_ui  # B04.13b\n"
    if "helpers.refresh_core import refresh_ui" not in pre:
        lines.insert(insert_idx, shim)
    new_pre = "\n".join(lines)
    # remove any trailing blank lines accumulation
    new_src = (new_pre.rstrip() + "\n\n" + post.lstrip())
    return new_src

def main():
    if not PANES.exists():
        print(f"[B04.13b] ERROR: {PANES} not found")
        return
    src = read_text(PANES)

    # If panes.py already imports refresh_ui from helper, do nothing
    if "helpers.refresh_core import refresh_ui" in src:
        print("[B04.13b] Already split â€” nothing to do.")
        return

    parts = extract_refresh(src)
    if parts == (None, None, None):
        print("[B04.13b] WARNING: refresh_ui(...) not found; no changes made.")
        return
    pre, func, post = parts

    # write helper file
    ensure_helper_file(func)

    # patch panes to import from helper
    new_src = patch_panes(pre, post)
    write_text(PANES, new_src)

    print(f"[B04.13b] Created/updated: {REFRESH_CORE}")
    print(f"[B04.13b] Patched:        {PANES}")

if __name__ == "__main__":
    main()

