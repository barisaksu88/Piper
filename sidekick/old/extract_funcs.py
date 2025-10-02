# extract_funcs.py
# Usage:
#   py extract_funcs.py <source_py> <archive_py> <func1> <func2> ...
#
# Moves the given top-level functions from source_py into archive_py,
# preserving original source text (incl. comments/docstrings) and
# writing a .bak of the source. Encoding-safe (handles UTF-8 BOM).

import sys, ast, io
from pathlib import Path

def main():
    if len(sys.argv) < 4:
        print("Usage: py extract_funcs.py <source_py> <archive_py> <func1> <func2> ...")
        sys.exit(1)

    src_path = Path(sys.argv[1])
    dst_path = Path(sys.argv[2])
    names = set(sys.argv[3:])

    text = src_path.read_text(encoding="utf-8-sig")  # tolerate BOM
    tree = ast.parse(text)

    # Collect top-level FunctionDef nodes to remove
    targets = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            # record exact source slice
            seg = ast.get_source_segment(text, node)
            # determine bounds (start at node.lineno-1; extend to just before next top-level node or EOF)
            targets.append((node.lineno, seg))

    if not targets:
        print("No matching top-level functions found.")
        return

    # Compute byte offsets for safe removal by using line numbers
    lines = text.splitlines(keepends=True)
    # Build list of (start_line_index, end_line_index_exclusive, src_segment_text, func_name)
    line_spans = []
    top_lines = len(lines)
    # Map line->node name to discover end boundaries
    func_positions = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_positions.append((node.lineno, node.name))
    func_positions.sort()

    name_by_line = {ln: nm for ln, nm in func_positions}
    # For each target, end at next top-level def/class start or EOF
    for start_ln, seg in targets:
        # find next boundary
        next_starts = [ln for ln, _ in func_positions if ln > start_ln]
        end_ln = next_starts[0] - 1 if next_starts else top_lines
        # include full lines
        start_idx = start_ln - 1
        end_idx_excl = end_ln
        # best-effort trim leading blank lines
        while start_idx > 0 and lines[start_idx-1].strip() == "":
            start_idx -= 1
        # capture name for log
        func_name = name_by_line.get(start_ln, "?")
        src_block = "".join(lines[start_idx:end_idx_excl])
        line_spans.append((start_idx, end_idx_excl, src_block, func_name))

    # Remove from source (process in reverse order)
    new_lines = lines[:]
    extracted = []
    for start_idx, end_idx_excl, src_block, func_name in sorted(line_spans, key=lambda t: t[0], reverse=True):
        extracted.append((func_name, src_block))
        del new_lines[start_idx:end_idx_excl]
        # also trim trailing consecutive blank lines left behind
        while start_idx < len(new_lines) and new_lines[start_idx].strip() == "":
            del new_lines[start_idx]

    # Write backups and outputs
    bak_path = src_path.with_suffix(src_path.suffix + ".bak")
    bak_path.write_text(text, encoding="utf-8")

    src_path.write_text("".join(new_lines), encoding="utf-8")

    # Ensure archive parent exists
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    # Append to archive file with a header
    header = f"# Extracted from {src_path} — kept for reference\n# Do NOT import from here at runtime.\n\n"
    if dst_path.exists():
        existing = dst_path.read_text(encoding="utf-8-sig")
        out = io.StringIO()
        out.write(existing)
        if not existing.endswith("\n"):
            out.write("\n")
    else:
        out = io.StringIO()
        out.write(header)

    for fname, block in reversed(extracted):  # keep original order
        out.write("\n# ---\n")
        out.write(block.rstrip() + "\n")

    dst_path.write_text(out.getvalue(), encoding="utf-8")
    print(f"Moved {len(extracted)} functions to {dst_path}")
    print(f"Updated {src_path} (backup at {bak_path})")

if __name__ == "__main__":
    main()
