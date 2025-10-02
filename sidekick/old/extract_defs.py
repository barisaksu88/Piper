# extract_defs.py
# Usage:
#   py extract_defs.py <source_py> <archive_py> <Name1> <Name2> ...
# Moves top-level FunctionDef/AsyncFunctionDef/ClassDef blocks from source to archive,
# preserving original text (comments/docstrings). Writes a .bak of the source.

import sys, ast, io, re
from pathlib import Path

def top_level_nodes(tree):
    return [(n.lineno, n) for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]

def main():
    if len(sys.argv) < 4:
        print("Usage: py extract_defs.py <source_py> <archive_py> <Name1> <Name2> ...")
        sys.exit(1)

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    names = set(sys.argv[3:])

    text = src.read_text(encoding="utf-8-sig")  # tolerate BOM
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    tops = sorted(top_level_nodes(tree), key=lambda t: t[0])

    # Map start line -> (name, kind, segment text, start_idx, end_idx_excl)
    captured = []
    for i, (ln, node) in enumerate(tops):
        node_name = getattr(node, "name", None)
        if not node_name or node_name not in names:
            continue
        seg = ast.get_source_segment(text, node) or ""
        start_idx = node.lineno - 1
        # end is just before next top-level node or EOF
        end_ln = (tops[i+1][0] - 1) if i + 1 < len(tops) else len(lines)
        end_idx = end_ln
        # extend forward through trailing blank lines
        while end_idx < len(lines) and lines[end_idx].strip() == "":
            end_idx += 1
        kind = node.__class__.__name__
        captured.append((node_name, kind, seg, start_idx, end_idx))

    if not captured:
        print("No matching top-level defs found.")
        return

    # Remove from source (reverse order)
    new_lines = lines[:]
    extracted = []
    for (name, kind, seg, start_idx, end_idx) in sorted(captured, key=lambda t: t[3], reverse=True):
        extracted.append((name, kind, "".join(new_lines[start_idx:end_idx])))
        del new_lines[start_idx:end_idx]
        # collapse extra blank lines
        while start_idx < len(new_lines) and new_lines[start_idx].strip() == "":
            del new_lines[start_idx]

    # Write backup + updated source
    src.with_suffix(src.suffix + ".bak").write_text(text, encoding="utf-8")
    # If file becomes empty or only whitespace, keep a tiny sentinel to avoid syntax errors
    body = "".join(new_lines)
    if not body.strip():
        body = "# (trimmed by extract_defs)\n"
    src.write_text(body, encoding="utf-8")

    # Append to archive with header
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        out = io.StringIO(dst.read_text(encoding="utf-8-sig"))
        if not out.getvalue().endswith("\n"):
            out.write("\n")
    else:
        out = io.StringIO()
        out.write(f"# Extracted from {src} — kept for reference\n# Do NOT import from here at runtime.\n\n")
    for name, kind, block in reversed(extracted):  # preserve original order
        out.write(f"\n# --- {kind} {name}\n")
        out.write(block.rstrip() + "\n")
    dst.write_text(out.getvalue(), encoding="utf-8")

    print(f"Moved {len(extracted)} defs to {dst}")
    print(f"Updated {src} (backup at {src.with_suffix(src.suffix + '.bak')})")

if __name__ == "__main__":
    main()
