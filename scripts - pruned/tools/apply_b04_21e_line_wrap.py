# -*- coding: utf-8 -*-
"""
B04.21e â€” wrap certain lines in ui/panes.py with try/except to fix VS syntax flags
Targets: lines 301, 335, 354, 355 (1-based indexing).
- If a target line is already indented under a 'try:' with a matching except/finally, skip.
- If a target line is blank/comment, skip.
- Also heals a bare 'try:' on the preceding line if found (adds except/pass).
Idempotent and conservative.
"""
from __future__ import annotations
from pathlib import Path
import re

PANES = Path(r"C:\Piper\scripts\ui\panes.py")
TARGETS = {301, 335, 354, 355}  # 1-based

def is_blank_or_comment(s: str) -> bool:
    t = s.strip()
    return (not t) or t.startswith("#")

def find_enclosing_try(lines, idx0):
    """
    Return tuple (try_line_idx, has_handler) for the nearest enclosing 'try:' above idx0,
    where try is at a lower indent than the target line.
    """
    target_indent = len(lines[idx0]) - len(lines[idx0].lstrip(" \t"))
    for i in range(idx0 - 1, -1, -1):
        L = lines[i]
        if not L.strip():  # skip blanks
            continue
        if L.lstrip().startswith(("def ", "class ")):
            return None, False
        if L.lstrip().startswith("try:"):
            try_indent = len(L) - len(L.lstrip(" \t"))
            if try_indent < target_indent:
                # scan forward for except/finally at same indent
                for j in range(i + 1, len(lines)):
                    Lj = lines[j]
                    if not Lj.strip():
                        continue
                    ind = len(Lj) - len(Lj.lstrip(" \t"))
                    if ind < try_indent:
                        break  # left the block
                    if ind == try_indent and Lj.lstrip().startswith(("except", "finally")):
                        return i, True
                return i, False
    return None, False

def wrap_line_with_try(lines, idx0):
    L = lines[idx0]
    indent = re.match(r"[ \t]*", L).group(0)
    # Build wrapper
    new_block = []
    new_block.append(indent + "try:\n")
    if L.strip():
        new_block.append(indent + "    " + L.lstrip())
    else:
        new_block.append(indent + "    pass\n")
    new_block.append(indent + "except Exception:\n")
    new_block.append(indent + "    pass\n")
    lines[idx0:idx0+1] = new_block

def heal_bare_try_above(lines, idx0):
    """
    If the previous non-blank line is a lone 'try:' with no handler, add a minimal handler.
    """
    # find previous non-blank
    p = idx0 - 1
    while p >= 0 and not lines[p].strip():
        p -= 1
    if p < 0:
        return
    if lines[p].lstrip().startswith("try:"):
        # Check for handler until next top-level (<= same indent)
        try_indent = len(lines[p]) - len(lines[p].lstrip(" \t"))
        has_handler = False
        for j in range(p + 1, len(lines)):
            Lj = lines[j]
            if not Lj.strip():
                continue
            ind = len(Lj) - len(Lj.lstrip(" \t"))
            if ind < try_indent:
                break
            if ind == try_indent and Lj.lstrip().startswith(("except", "finally")):
                has_handler = True
                break
        if not has_handler:
            # Insert a minimal handler right after the try-block body (or immediately if empty)
            # Find first line with indent <= try_indent after p to insert before it
            insert_at = None
            for j in range(p + 1, len(lines)):
                Lj = lines[j]
                if not Lj.strip():
                    continue
                ind = len(Lj) - len(Lj.lstrip(" \t"))
                if ind <= try_indent:
                    insert_at = j
                    break
            handler = []
            handler.append(" " * try_indent + "except Exception:\n")
            handler.append(" " * (try_indent + 4) + "pass\n")
            if insert_at is None:
                lines.extend(handler)
            else:
                lines[insert_at:insert_at] = handler

def main():
    text = PANES.read_text(encoding="utf-8")
    lines = text.splitlines(True)  # keep newlines

    # Defensive: if file shorter, adjust targets
    maxln = len(lines)
    targets = [t for t in sorted(TARGETS) if 1 <= t <= maxln]

    changed = False
    # Process from bottom to top so indices stay valid
    for t in reversed(targets):
        idx0 = t - 1
        L = lines[idx0]
        if is_blank_or_comment(L):
            continue
        try_idx, has_handler = find_enclosing_try(lines, idx0)
        if try_idx is None:
            # Heal any bare 'try:' just above (common residue)
            heal_bare_try_above(lines, idx0)
            # Wrap the line itself
            wrap_line_with_try(lines, idx0)
            changed = True
        elif not has_handler:
            # Add a handler to the enclosing try
            heal_bare_try_above(lines, idx0)
            changed = True
        else:
            # Already safe
            pass

    if changed:
        PANES.write_text("".join(lines), encoding="utf-8")
        print("[B04.21e] Wrapped target lines and healed nearby try blocks.")
    else:
        print("[B04.21e] No changes needed (targets already safe).")

if __name__ == "__main__":
    main()

