# C:\Piper\scripts\tools\make_callgraph.py
import os, sys, ast, pathlib, re
from collections import defaultdict

PKG_NAMES = {"entries","core","ui","services","logic"}
OUT_MERMAID = "callgraph.mmd"
OUT_DOT = "callgraph.dot"
OUT_EDGES = "callgraph_edges.txt"

def is_pkg_module(name:str)->bool:
    root = name.split('.',1)[0]
    return root in PKG_NAMES

def module_name_from_path(root:str, path:str)->str:
    rel = pathlib.Path(path).resolve().relative_to(pathlib.Path(root).resolve())
    parts = list(rel.parts)
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)

def scan_file(root, path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return set(), {}, set()
    imports = set()
    alias_map = {}  # local name -> module or attr origin (module.attr)
    defined_funcs = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            defined_funcs.add(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if is_pkg_module(a.name):
                    imports.add(a.name)
                    if a.asname:
                        alias_map[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.module and is_pkg_module(node.module):
                mod = node.module
                imports.add(mod)
                for a in node.names:
                    local = a.asname or a.name
                    alias_map[local] = f"{mod}.{a.name}"

    # infer cross-module calls for `from X import Y` then `Y(...)`
    calls = set()
    class CallVisitor(ast.NodeVisitor):
        def visit_Call(self, call):
            # bare name calls
            if isinstance(call.func, ast.Name):
                name = call.func.id
                origin = alias_map.get(name)
                if origin and "." in origin:
                    mod = origin.rsplit(".",1)[0]
                    if is_pkg_module(mod):
                        calls.add(mod)
            # attribute calls like mod.func()
            elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                base = call.func.value.id
                origin = alias_map.get(base)
                if origin and is_pkg_module(origin):
                    calls.add(origin)
            self.generic_visit(call)

    CallVisitor().visit(tree)
    return imports, alias_map, calls

def build_graph(root):
    edges = defaultdict(set)
    files = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.endswith(".py"):
                files.append(os.path.join(dirpath, fn))
    for path in files:
        mod = module_name_from_path(root, path)
        imports, alias_map, calls = scan_file(root, path)
        for dst in imports | calls:
            edges[mod].add(dst)
    return edges

def write_mermaid(edges, out_path):
    lines = ["flowchart LR"]
    for src, dsts in edges.items():
        for dst in sorted(dsts):
            lines.append(f'  {src.replace(".","_")}["{src}"] --> {dst.replace(".","_")}["{dst}"]')
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def write_dot(edges, out_path):
    lines = ["digraph G {"]
    for src, dsts in edges.items():
        for dst in sorted(dsts):
            lines.append(f'  "{src}" -> "{dst}";')
    lines.append("}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def write_edges_list(edges, out_path):
    lines = []
    for src, dsts in sorted(edges.items()):
        for dst in sorted(dsts):
            lines.append(f"{src} -> {dst}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    edges = build_graph(root)
    write_mermaid(edges, OUT_MERMAID)
    write_dot(edges, OUT_DOT)
    write_edges_list(edges, OUT_EDGES)
    print(f"âœ… Wrote {OUT_MERMAID}, {OUT_DOT}, {OUT_EDGES}")

if __name__ == "__main__":
    main()

