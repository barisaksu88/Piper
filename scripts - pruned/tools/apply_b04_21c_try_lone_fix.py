# -*- coding: utf-8 -*-
"""
B04.21c â€” fix lone/indented 'try:' before viewport lines in ui/panes.py
- Finds a 'try:' (any leading spaces) immediately followed by top-level viewport lines
  like get_viewport_client_width/height or configure_item size calls, without except/finally.
- Replaces that pattern with a safe try/except wrapper.
- Idempotent and conservative.
"""
from __future__ import annotations
import re
from pathlib import Path

PANES = Path(r"C:\Piper\scripts\ui\panes.py")

def main():
    src = PANES.read_text(encoding="utf-8")
    orig = src

    # Pattern: optional leading spaces + 'try:' + newline + one or more "viewport-ish" lines
    # that are NOT indented (top-level), and then a next top-level token (def/class/from/import/try/except/finally)
    v_needles = r"(?:get_viewport_client_width\(\)|get_viewport_client_height\(\)|set_item_width\(|set_item_height\(|configure_item\()"
    pat = re.compile(
        rf"(?P<indent>^[ \t]*)try:\s*\r?\n"                         # lone try (any indent)
        rf"(?P<body>(?:^(?![ \t]).*{v_needles}.*\r?\n)+)"           # one+ top-level viewport lines
        rf"(?=(?:^(?:def |class |from |import |try:|except|finally)|\Z))",  # stop before next top-level or EOF
        re.MULTILINE
    )

    def repl(m: re.Match) -> str:
        indent = m.group("indent")
        body = m.group("body")
        # Wrap body in a proper try/except with same leading indent
        wrapped = f"{indent}try:\n" + "".join(f"{indent}    {L}" for L in body.splitlines(True))
        wrapped += f"{indent}except Exception:\n{indent}    pass\n"
        return wrapped

    src_new, n = pat.subn(repl, src)
    if n:
        PANES.write_text(src_new, encoding="utf-8")
        print(f"[B04.21c] Rewrapped lone viewport try-block (x{n}).")
    else:
        print("[B04.21c] No matching lone viewport try-block found.")

if __name__ == "__main__":
    main()

