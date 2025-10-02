# -*- coding: utf-8 -*-
"""
B04.21g â€” harden line ~320 in scripts/ui/panes.py
- If line 320 is 'try:' (ignoring whitespace), replace it with:
      try:
          pass
      except Exception:
          pass
- If not exactly on 320, scan Â±10 lines around 320 for a bare 'try:' with no matching except
  before next top-level 'def/class/from/import/try/except/finally', and fix that one.
- Idempotent and conservative (only one patch per run).
"""
from __future__ import annotations
from pathlib import Path
import re

PANES = Path(r"C:\Piper\scripts\ui\panes.py")

def looks_like_top_level(s: str) -> bool:
    t = s.lstrip()
    return t.startswith(("def ","class ","from ","import ","try:","except","finally"))

def main():
    text = PANES.read_text(encoding="utf-8")
    lines = text.splitlines(True)  # keep EOLs
    n = len(lines)

    def patch_at(idx: int) -> bool:
        """idx is 0-based index of the suspected 'try:' line"""
        if idx < 0 or idx >= n:
            return False
        if lines[idx].rstrip().lstrip() != "try:":
            return False

        # Check if a matching except/finally exists before next top-level stmt at same indent
        try_indent = len(lines[idx]) - len(lines[idx].lstrip(" \t"))
        has_handler = False
        j = idx + 1
        while j < n:
            Lj = lines[j]
            if not Lj.strip():
                j += 1
                continue
            ind = len(Lj) - len(Lj.lstrip(" \t"))
            if ind < try_indent:
                break  # left the block
            if ind == try_indent and Lj.lstrip().startswith(("except","finally")):
                has_handler = True
                break
            if looks_like_top_level(Lj) and ind == 0:
                break
            j += 1

        if has_handler:
            return False  # already safe

        # Replace single line with a full minimal try/except block
        indent = lines[idx][:try_indent]
        replacement = [
            f"{indent}try:\n",
            f"{indent}    pass\n",
            f"{indent}except Exception:\n",
            f"{indent}    pass\n",
        ]
        lines[idx:idx+1] = replacement
        return True

    # First try exact line 320 (1-based -> index 319)
    changed = patch_at(319)

    # If not changed, search a small window Â±10 lines
    if not changed:
        start = max(0, 319 - 10)
        end   = min(n, 319 + 10 + 1)
        for k in range(start, end):
            if patch_at(k):
                changed = True
                break

    if changed:
        PANES.write_text("".join(lines), encoding="utf-8")
        print("[B04.21g] Patched bare 'try:' near line 320.")
    else:
        print("[B04.21g] No changes applied (no bare 'try:' found near 320).")

if __name__ == "__main__":
    main()

