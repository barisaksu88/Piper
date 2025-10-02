# -*- coding: utf-8 -*-
"""
B04.21f â€” strict fix for lone 'try:' in scripts/ui/panes.py
- Finds any line that's just 'try:' (any indent), followed only by blank lines or EOF,
  or followed immediately by a new top-level statement (def/class/from/import/try/except/finally).
- Forces a minimal body+handler:
      try:
          pass
      except Exception:
          pass
- Idempotent.
"""
from __future__ import annotations
from pathlib import Path
import re

PANES = Path(r"C:\Piper\scripts\ui\panes.py")

def main():
    s = PANES.read_text(encoding="utf-8")
    orig = s

    # Case 1: 'try:' followed by blank lines and then a new top-level statement or EOF
    # Replace the 'try:' + blank-run with a full minimal try/except.
    pat_blank_then_top = re.compile(
        r"(?P<indent>^[ \t]*)try:\s*\r?\n"          # try:
        r"(?P<blanks>(?:^[ \t]*\r?\n)*)"            # any blank lines
        r"(?=(?:^(?:def |class |from |import |try:|except|finally)|\Z))",  # next top-level or EOF
        re.MULTILINE
    )
    def repl_blank(m):
        indent = m.group("indent")
        return (
            f"{indent}try:\n"
            f"{indent}    pass\n"
            f"{indent}except Exception:\n"
            f"{indent}    pass\n"
        )

    s, n1 = pat_blank_then_top.subn(repl_blank, s)

    # Case 2: truly orphan 'try:' lines with *no* following body before a non-indented line.
    # (Safety overlap with case 1; keep both.)
    pat_lone = re.compile(r"(?P<indent>^[ \t]*)try:\s*(?=^\S|\Z)", re.MULTILINE)
    s, n2 = pat_lone.subn(lambda m:
        f"{m.group('indent')}try:\n{m.group('indent')}    pass\n{m.group('indent')}except Exception:\n{m.group('indent')}    pass\n", s)

    if s != orig:
        PANES.write_text(s, encoding="utf-8")
        print(f"[B04.21f] Fixed lone 'try:' (case1={n1}, case2={n2}).")
    else:
        print("[B04.21f] No changes needed.")

if __name__ == "__main__":
    main()

