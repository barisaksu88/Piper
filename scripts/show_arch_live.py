#!/usr/bin/env python3
"""
Export a readable "architecture tree" of C:\Piper\scripts into a UTF-8 text file.

Default behavior (double-click or `python export_piper_architecture.py`):
  - Scans:   C:\Piper\scripts
  - Writes:  C:\Piper\Piper_Scripts_Architecture_<YYYY-MM-DD_HHMMSS>.txt
  - Encoding: UTF-8
  - Ignores: __pycache__, *.pyc, .git, .venv, venv, node_modules, etc.
  - Shows:   directories first, then files (with sizes)

You can override root/output via CLI:
  python export_piper_architecture.py --root "D:\Some\Path" --out "D:\tree.txt" --no-sizes --max-depth 5
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path

# ---------- Defaults ----------
DEFAULT_ROOT = Path(r"C:\Piper\scripts")
DEFAULT_OUT_DIR = Path(r"C:\Piper")
DEFAULT_ENCODING = "utf-8"  # If Notepad shows oddities, try "utf-8-sig"

# Names and extensions to ignore
IGNORE_DIR_NAMES = {
    "__pycache__", ".git", ".hg", ".svn", ".idea", ".vscode",
    "venv", ".venv", "env", ".mypy_cache", ".pytest_cache", "node_modules"
}
IGNORE_FILE_EXTS = {".pyc", ".pyo", ".pyd", ".log"}
IGNORE_FILE_NAMES = {"desktop.ini", "Thumbs.db"}


def human_size(n_bytes: int) -> str:
    """Return a human-friendly file size string."""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n_bytes)
    for u in units:
        if size < 1024 or u == units[-1]:
            return f"{size:.0f} {u}" if u == "B" else f"{size:.1f} {u}"
        size /= 1024.0


def _safe_stat(path: Path):
    try:
        return path.stat()
    except Exception:
        return None


def list_children_sorted(path: Path):
    """Yield (dirs, files) sorted case-insensitively, dirs first then files."""
    dirs, files = [], []
    try:
        for p in path.iterdir():
            name = p.name
            if p.is_dir():
                if name in IGNORE_DIR_NAMES:
                    continue
                dirs.append(p)
            else:
                if name in IGNORE_FILE_NAMES or p.suffix.lower() in IGNORE_FILE_EXTS:
                    continue
                files.append(p)
    except PermissionError:
        return [], []
    key = lambda p: p.name.lower()
    return sorted(dirs, key=key), sorted(files, key=key)


def draw_tree(root: Path, include_sizes: bool = True, max_depth: int | None = None) -> list[str]:
    """
    Build a pretty directory tree as a list of lines using Unicode box characters.
    Example:
    scripts
    ├── core
    │   └── engine.py (12.4 KB)
    └── ui
        ├── ...
    """
    lines: list[str] = []
    root_name = root.name or str(root)
    lines.append(root_name)

    def recurse(cur: Path, prefix: str, depth: int):
        if max_depth is not None and depth > max_depth:
            return

        dirs, files = list_children_sorted(cur)
        entries = dirs + files
        total = len(entries)

        for i, entry in enumerate(entries):
            connector = "└── " if i == total - 1 else "├── "
            is_last = (i == total - 1)
            next_prefix = prefix + ("    " if is_last else "│   ")

            if entry.is_dir():
                line = f"{prefix}{connector}{entry.name}"
                lines.append(line)
                recurse(entry, next_prefix, depth + 1)
            else:
                if include_sizes:
                    st = _safe_stat(entry)
                    size_str = f" ({human_size(st.st_size)})" if st else " (?)"
                else:
                    size_str = ""
                lines.append(f"{prefix}{connector}{entry.name}{size_str}")

    recurse(root, prefix="", depth=1)
    return lines


def write_header(root: Path) -> list[str]:
    now = _dt.datetime.now()
    hdr = [
        "Piper — Scripts Architecture Export",
        f"Root:      {root}",
        f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Ignores:   {', '.join(sorted(IGNORE_DIR_NAMES))} (dirs), "
        f"{', '.join(sorted(IGNORE_FILE_EXTS)) or '-'} (exts)",
        "",
    ]
    return hdr


def ensure_existing_dir(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Warning: could not create directory {path.parent}: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Export UTF-8 text tree of a source directory.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Root folder to scan")
    parser.add_argument("--out", type=Path, default=None, help="Output .txt path")
    parser.add_argument("--encoding", default=DEFAULT_ENCODING, help="Text encoding (default: utf-8)")
    parser.add_argument("--no-sizes", action="store_true", help="Do not include file sizes")
    parser.add_argument("--max-depth", type=int, default=None, help="Limit recursion depth (1 = only root)")
    args = parser.parse_args()

    root: Path = args.root
    if not root.exists() or not root.is_dir():
        print(f"ERROR: Root folder does not exist or is not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    if args.out is None:
        stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_path = (DEFAULT_OUT_DIR / f"Piper_Scripts_Architecture_{stamp}.txt")
    else:
        out_path = args.out

    # Build content
    header = write_header(root)
    tree_lines = draw_tree(root, include_sizes=not args.no_sizes, max_depth=args.max_depth)
    content = "\n".join(header + tree_lines) + "\n"

    # Write file (UTF-8)
    ensure_existing_dir(out_path)
    try:
        with open(out_path, "w", encoding=args.encoding, newline="\n") as f:
            f.write(content)
    except Exception as e:
        print(f"ERROR: Could not write output file: {out_path}\n{e}", file=sys.stderr)
        sys.exit(2)

    print(f"✅ Architecture written to: {out_path}")
    print(f"   Encoding: {args.encoding}")
    print(f"   Root:     {root}")


if __name__ == "__main__":
    # Make double-click friendly on Windows by pausing if an error occurs
    try:
        main()
    except SystemExit as e:
        # propagate exit code
        if e.code != 0:
            if os.name == "nt" and sys.stdout.isatty() is False:
                os.system("pause")
        raise
    except Exception as ex:
        print(f"Unhandled error: {ex}", file=sys.stderr)
        if os.name == "nt" and sys.stdout.isatty() is False:
            os.system("pause")
        sys.exit(3)
