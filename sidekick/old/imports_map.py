# imports_map.py — quick import graph for Piper (no Graphviz needed)
# Run from C:\Piper (venv). Outputs to stdout; redirect to sidekick\deps.txt
import os, ast, sys, pathlib

ROOT = pathlib.Path(r"C:\Piper\scripts")
PKG  = "scripts"

def mod_name(path: pathlib.Path) -> str:
    rel = path.relative_to(ROOT.parent)  # C:\Piper\scripts -> C:\Piper
    parts = rel.with_suffix("").parts
    return ".".join(parts)

def is_local(mod: str) -> bool:
    return mod.startswith(PKG + ".")

edges = {}
files = [p for p in ROOT.rglob("*.py") if p.is_file()]
for f in files:
    try:
        src = f.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src, filename=str(f))
    except Exception:
        continue
    src_mod = mod_name(f)
    deps = edges.setdefault(src_mod, set())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                name = n.name
                if is_local(name):
                    deps.add(name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                base = node.module
                # handle relative imports: from .foo import bar
                if node.level and src_mod.startswith(PKG):
                    pkg_parts = src_mod.split(".")
                    # strip module filename for relative anchor
                    if pkg_parts[-1] != "__init__":
                        pkg_parts = pkg_parts[:-1]
                    # walk up
                    anchor = pkg_parts[: max(0, len(pkg_parts) - node.level)]
                    base = ".".join(anchor + [node.module]) if anchor else f"{PKG}.{node.module}"
                if is_local(base):
                    deps.add(base)
    edges[src_mod] = deps

print("# Piper import graph (local modules only)\n")
for src, deps in sorted(edges.items()):
    if not deps:
        continue
    for dst in sorted(deps):
        print(f"{src} -> {dst}")
