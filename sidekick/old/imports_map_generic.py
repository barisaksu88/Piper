# imports_map_generic.py — robust local import graph with debug fallback
# Usage:
#   py C:\Piper\sidekick\imports_map_generic.py C:\Piper\analyzer_sandbox > C:\Piper\sidekick\deps_test.txt
#   py C:\Piper\sidekick\imports_map_generic.py C:\Piper\scripts > C:\Piper\sidekick\deps.txt
import sys, ast, pathlib, re

DEBUG = True  # flip to True if you want per-file notes in output

def mod_name(root: pathlib.Path, path: pathlib.Path) -> str:
    rel = path.relative_to(root.parent)  # e.g. C:\Piper\analyzer_sandbox\mod_a.py -> analyzer_sandbox\mod_a.py
    return ".".join(rel.with_suffix("").parts)

def resolve_relative(base_mod: str, module: str | None, level: int, root_pkg: str) -> str | None:
    if module is None:
        module = ""
    if level <= 0:
        return module
    parts = base_mod.split(".")
    if parts and parts[-1] != "__init__":
        parts = parts[:-1]
    if level > len(parts):
        return f"{root_pkg}.{module}" if module else root_pkg
    anchor = parts[: len(parts) - level]
    mod = ".".join([*anchor, module]) if module else ".".join(anchor)
    return mod

def is_local(root_pkg: str, name: str | None) -> bool:
    return bool(name) and (name == root_pkg or name.startswith(root_pkg + "."))

def ast_edges_for_file(root, f, root_pkg):
    try:
        txt = f.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(txt, filename=str(f))
    except Exception:
        return set()
    src_mod = mod_name(root, f)
    deps = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                name = n.name
                if is_local(root_pkg, name):
                    deps.add(name)
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(src_mod, node.module, getattr(node, "level", 0), root_pkg)
            if is_local(root_pkg, base):
                deps.add(base)
    return deps

def regex_edges_for_file(root, f, root_pkg):
    # super-simple fallback if AST finds nothing
    try:
        txt = f.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()
    src_mod = mod_name(root, f)
    deps = set()
    # from analyzer_sandbox.mod_b import greet
    for m in re.finditer(rf"\bfrom\s+{re.escape(root_pkg)}\.[A-Za-z0-9_\.]+\s+import\b", txt):
        frag = m.group(0).split()[1]  # the module
        deps.add(frag)
    # import analyzer_sandbox.mod_b
    for m in re.finditer(rf"\bimport\s+{re.escape(root_pkg)}\.[A-Za-z0-9_\.]+", txt):
        frag = m.group(0).split()[1]
        deps.add(frag)
    return deps

def main():
    if len(sys.argv) != 2:
        print("usage: imports_map_generic.py <root_folder>", file=sys.stderr)
        sys.exit(2)
    root = pathlib.Path(sys.argv[1]).resolve()
    if not root.exists() or not root.is_dir():
        print(f"root not found: {root}", file=sys.stderr); sys.exit(3)

    root_pkg = root.name
    files = sorted(p for p in root.rglob("*.py") if p.is_file())

    edges = {}
    debug_lines = []

    for f in files:
        src = mod_name(root, f)
        deps_ast = ast_edges_for_file(root, f, root_pkg)
        deps_rgx = set()
        if not deps_ast:
            deps_rgx = regex_edges_for_file(root, f, root_pkg)
        deps = deps_ast | deps_rgx
        if DEBUG:
            debug_lines.append(f"# {src}: ast={sorted(deps_ast)} regex={sorted(deps_rgx)}")
        if deps:
            edges.setdefault(src, set()).update(deps)

    print(f"# Import edges under {root_pkg}\n")
    printed = 0
    for src in sorted(edges):
        for dst in sorted(edges[src]):
            print(f"{src} -> {dst}")
            printed += 1
    if printed == 0:
        print("(no local edges found)")
    if DEBUG and debug_lines:
        print("\n# DEBUG\n" + "\n".join(debug_lines))

if __name__ == "__main__":
    main()
