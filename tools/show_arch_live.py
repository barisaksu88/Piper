#!/usr/bin/env python3
# show_arch_live.py — Realtime architecture viewer for Piper (no external deps).
# - Prints a project tree and project‑local import graph.
# - Watch mode auto‑refreshes on file changes.
# - Optional GUI with DearPyGui (falls back to console if unavailable).

import os
import sys
import time
import ast
import hashlib
import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple

IGNORES = {"__pycache__", ".git", ".idea", ".vscode", "venv", "venv310", "runtime", ".restart", "packages", "temp"}

def rel_module_name(root: Path, file: Path) -> str:
    rel = file.relative_to(root).as_posix()
    if rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")

def collect_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for p in root.rglob("*.py"):
        if any(seg in IGNORES for seg in p.parts):
            continue
        files.append(p)
    return sorted(files)

def hash_files(files: List[Path]) -> str:
    h = hashlib.sha1()
    for f in files:
        try:
            st = f.stat()
            h.update(f.name.encode("utf-8", "ignore"))
            h.update(str(int(st.st_mtime)).encode())
            h.update(str(st.st_size).encode())
        except Exception:
            pass
    return h.hexdigest()

def parse_imports(py_path: Path) -> Set[str]:
    try:
        src = py_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(py_path))
    except Exception:
        return set()
    mods: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module.split(".")[0])
    return {m for m in mods if m}

def build_import_graph(root: Path) -> Tuple[Dict[str, Set[str]], Dict[str, Path]]:
    files = collect_files(root)
    modules = {rel_module_name(root, f): f for f in files}
    roots = set(m.split(".")[0] for m in modules)

    graph: Dict[str, Set[str]] = {m: set() for m in modules}
    for m, f in modules.items():
        for imp in parse_imports(f):
            # only keep edges to project‑local roots
            if imp in roots:
                graph[m].add(imp)
    return graph, modules

def ascii_tree(root: Path) -> str:
    lines: List[str] = []
    def walk(p: Path, prefix: str = ""):
        entries = [e for e in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
                   if e.name not in IGNORES and not e.name.startswith(".")]
        for i, e in enumerate(entries):
            connector = "└── " if i == len(entries)-1 else "├── "
            if e.is_dir():
                lines.append(prefix + connector + e.name + "/")
                walk(e, prefix + ("    " if i == len(entries)-1 else "│   "))
            else:
                if e.suffix == ".py":
                    lines.append(prefix + connector + e.name)
    walk(root)
    return "\n".join(lines)

def emit_mermaid(graph: Dict[str, Set[str]]) -> str:
    lines = ["```mermaid", "graph LR"]
    for src, tgts in graph.items():
        for dst in tgts:
            lines.append(f"  {src.replace('.', '_')} --> {dst.replace('.', '_')}")
    lines.append("```")
    return "\n".join(lines)

def emit_dot(graph: Dict[str, Set[str]]) -> str:
    lines = ["digraph Piper {", '  graph [rankdir=LR];']
    for src, tgts in graph.items():
        s = src.replace(".", "_")
        lines.append(f'  "{s}" [shape=box];')
        for dst in tgts:
            d = dst.replace(".", "_")
            lines.append(f'  "{s}" -> "{d}";')
    lines.append("}")
    return "\n".join(lines)

def print_report(root: Path, mermaid: Path = None, dot: Path = None):
    graph, modules = build_import_graph(root)
    tree = ascii_tree(root)

    os.system("cls" if os.name == "nt" else "clear")
    print("PIPER ARCHITECTURE @", time.strftime("%H:%M:%S"))
    print("Root:", root)
    print("\n[Project Tree]\n")
    print(tree or "(no files)")

    print("\n[Import Graph] (project‑local roots)\n")
    for m in sorted(graph):
        tgts = sorted(graph[m])
        if tgts:
            print(f"  {m} -> {', '.join(tgts)}")

    print("\n[Stats]")
    print("  Files:", len(modules))
    edges = sum(len(v) for v in graph.values())
    print("  Import edges (project‑local):", edges)

    if mermaid:
        mermaid.write_text(emit_mermaid(graph), encoding="utf-8")
        print(f"\nWrote Mermaid graph to: {mermaid}")
    if dot:
        dot.write_text(emit_dot(graph), encoding="utf-8")
        print(f"Wrote DOT graph to: {dot}")

def main():
    ap = argparse.ArgumentParser(description="Realtime Piper architecture viewer")
    ap.add_argument("--root", type=str, default=".", help="Project root (e.g., C:\\Piper\\scripts)")
    ap.add_argument("--once", action="store_true", help="Render once and exit")
    ap.add_argument("--watch", action="store_true", help="Poll for changes and refresh")
    ap.add_argument("--interval", type=float, default=2.0, help="Watch poll seconds")
    ap.add_argument("--mermaid", type=str, default="", help="Write Mermaid graph to this file")
    ap.add_argument("--dot", type=str, default="", help="Write Graphviz DOT to this file")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print("Root does not exist:", root)
        sys.exit(1)

    mermaid = Path(args.mermaid) if args.mermaid else None
    dot = Path(args.dot) if args.dot else None

    if args.once and not args.watch:
        print_report(root, mermaid=mermaid, dot=dot)
        return

    last_hash = ""
    while True:
        files = collect_files(root)
        h = hash_files(files)
        if h != last_hash:
            last_hash = h
            print_report(root, mermaid=mermaid, dot=dot)
        if not args.watch:
            break
        time.sleep(max(0.2, float(args.interval)))

if __name__ == "__main__":
    main()