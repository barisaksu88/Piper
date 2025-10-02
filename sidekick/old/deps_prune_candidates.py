# deps_prune_candidates.py — propose safe archive candidates from deps.txt
# - Reads C:\Piper\sidekick\deps.txt (lines like "a.b -> c.d")
# - Scans C:\Piper\scripts filesystem for all modules
# - Roots/entrypoints: scripts/entries/*.py, any file with __main__, and package __init__.py
# - Output: C:\Piper\sidekick\prune_report.md (no changes to code)

import pathlib, re, sys, ast

ROOT = pathlib.Path(r"C:\Piper")
SRC  = ROOT / "scripts"
SIDE = ROOT / "sidekick"
DEPS = SIDE / "deps.txt"
OUT  = SIDE / "prune_report.md"

def list_modules():
    """Return dict: module_name -> path"""
    mods = {}
    for p in SRC.rglob("*.py"):
        # turn C:\Piper\scripts\pkg\m.py into scripts.pkg.m
        rel = p.relative_to(ROOT.parent)  # …\Piper\scripts -> relative from C:\Piper
        mod = ".".join(rel.with_suffix("").parts)
        mods[mod] = p
    return mods

def parse_deps():
    edges = []
    if not DEPS.exists():
        return edges
    for ln in DEPS.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "->" not in ln: continue
        a,b = [x.strip() for x in ln.split("->",1)]
        if a and b: edges.append((a,b))
    return edges

def entrypoint_like(path: pathlib.Path) -> bool:
    # Entrypoints: scripts/entries/*.py
    if (SRC / "entries") in path.parents:
        return True
    # Package initializers: __init__.py (keep packages)
    if path.name == "__init__.py":
        return True
    # Files with if __name__ == "__main__"
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
        if "__name__" in txt and "__main__" in txt:
            # quick AST check to reduce false positives
            try:
                tree = ast.parse(txt)
                for n in ast.walk(tree):
                    if isinstance(n, ast.If):
                        try:
                            cond = ast.get_source_segment(txt, n.test) or ""
                        except Exception:
                            cond = ""
                        if "__name__" in cond and "__main__" in cond:
                            return True
            except Exception:
                # fallback: raw heuristic
                return True
    except Exception:
        pass
    return False

def sccs(nodes, edges):
    """Tarjan’s algorithm for strongly connected components."""
    g = {n: [] for n in nodes}
    for a,b in edges:
        if a in g: g[a].append(b)
    idx = 0
    S, onS = [], set()
    index, lowlink = {}, {}
    comps = []

    def dfs(v):
        nonlocal idx
        index[v] = idx; lowlink[v] = idx; idx += 1
        S.append(v); onS.add(v)
        for w in g.get(v, []):
            if w not in index:
                dfs(w); lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in onS:
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            comp = []
            while True:
                w = S.pop(); onS.discard(w); comp.append(w)
                if w == v: break
            comps.append(comp)

    for v in nodes:
        if v not in index:
            dfs(v)
    return comps

def main():
    mods = list_modules()                 # module -> path
    edges = parse_deps()                  # (a -> b)
    all_nodes = set(mods.keys())
    incoming = {m:0 for m in all_nodes}
    outgoing = {m:0 for m in all_nodes}

    for a,b in edges:
        if a in outgoing: outgoing[a]+=1
        if b in incoming: incoming[b]+=1

    # Roots/entrypoints (never delete)
    roots = set()
    for m,p in mods.items():
        if entrypoint_like(p):
            roots.add(m)

    # Orphans: no incoming refs
    orphans = [m for m in all_nodes if incoming[m]==0]

    # Safe archive candidates = orphans NOT in roots and NOT packages that have used submodules
    # (i.e., if scripts.foo is orphan but scripts.foo.bar exists and is used, keep the package)
    used = set()
    for _,b in edges:
        used.add(b)
    # mark parent packages of any used module
    used_pkgs = set()
    for u in used:
        parts = u.split(".")
        for i in range(1,len(parts)):
            used_pkgs.add(".".join(parts[:i]))

    candidates = []
    for m in sorted(orphans):
        p = mods[m]
        if m in roots:            # entrypoint-like
            continue
        if m in used_pkgs:        # parent package of used modules
            continue
        candidates.append((m,p))

    # Small SCCs (cycles <= 4) to inspect (deleting one file in a cycle can be risky)
    comps = sccs(all_nodes, edges)
    cycles = [c for c in comps if len(c) > 1 and len(c) <= 4]

    # Heavy orphans (big files)
    heavy = []
    for m,p in candidates:
        try:
            kb = p.stat().st_size/1024
            if kb >= 25:  # threshold: 25KB
                heavy.append((m,p,kb))
        except Exception:
            pass

    # Write report
    lines=[]
    lines.append("# Piper — Prune Candidates (read-only)\n")
    lines.append("**Rules:** Orphans = no incoming imports. Do NOT delete anything under entries/ or main scripts. First move candidates to archive and re-run tests.\n")
    lines.append("\n## Roots / Entrypoints (never delete)\n")
    for r in sorted(roots):
        lines.append(f"- {r}  [{mods[r]}]")

    lines.append("\n## Orphans (no incoming deps) — raw\n")
    for m,p in candidates:
        lines.append(f"- {m}  [{p}]")

    lines.append("\n## Heavy Orphans (>=25KB) — prioritize\n")
    for m,p,kb in sorted(heavy, key=lambda x: -x[2]):
        lines.append(f"- {m}  [{p}]  ~{kb:.1f} KB")

    lines.append("\n## Small Cycles (size ≤ 4) — inspect before pruning\n")
    for comp in cycles[:40]:
        lines.append(f"- {'  <->  '.join(comp)}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT}")

if __name__ == "__main__":
    main()
