# scripts/tools/clean_inventory.py
# UTF-8 inventory for Piper cleanup rail (B04 prep)
from __future__ import annotations
import argparse, os, sys, shutil
from pathlib import Path

NEEDLES = [
    "set_state_dot","update_state_dot","state_dot_circle","state_label","state_text",
    "current_state","_pending_state_for_ui","_waking_seen_ts","VALID_STATES",
    "STATE_RE","STATE_WORD_RE","SLEEP_HINT_RE",
    "refresh_ui","apply_header_updates","header_bridge",
    "dev_tools","dev_controls_mount",
    "persona_tone","persona_sarcasm",
]

def _read_text(p: Path) -> str:
    # resilient reader for mixed encodings
    for enc in ("utf-8", "cp1254", "latin-1"):
        try:
            return p.read_text(encoding=enc, errors="ignore")
        except Exception:
            pass
    return p.read_bytes().decode("utf-8", "ignore")

def _write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8", newline="\n")

def _collect_py_files(root: Path) -> list[Path]:
    return sorted(
        Path(dp).joinpath(fn)
        for dp, _, fns in os.walk(root)
        for fn in fns
        if fn.endswith(".py")
    )

def snapshot_sizes(py: list[Path], out_path: Path) -> None:
    rows = []
    for f in py:
        try:
            sz = f.stat().st_size
        except Exception:
            sz = -1
        rows.append(f"{f}\t{sz}")
    rows.sort()
    _write_text(out_path, "\n".join(rows))

def build_symbol_map(py: list[Path], out_path: Path) -> None:
    rows = []
    for needle in NEEDLES:
        seen = set()
        for f in py:
            try:
                txt = _read_text(f)
            except Exception:
                continue
            if needle in txt:
                seen.add(str(f))
        for fp in sorted(seen):
            rows.append(f"{needle}\t{fp}")
    rows.sort()
    _write_text(out_path, "\n".join(rows))

def dump_contexts(py: list[Path], out_dir: Path, ctx: int = 5) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for needle in NEEDLES:
        chunks = []
        for f in py:
            try:
                lines = _read_text(f).splitlines()
            except Exception:
                continue
            for idx, line in enumerate(lines, start=1):
                if needle in line:
                    start = max(1, idx - ctx)
                    end   = min(len(lines), idx + ctx)
                    snippet = "\n".join(f"{i:6d}: {lines[i-1]}" for i in range(start, end+1))
                    chunks.append(
                        f"{'-'*80}\nFILE: {f}\nHIT: {needle} @ line {idx}\n{'-'*80}\n{snippet}\n"
                    )
        _write_text(out_dir / f"{needle}.txt", "\n".join(chunks))

def copy_to_mirrors(artifacts: list[Path], mirrors: list[Path]) -> None:
    for m in mirrors:
        m.mkdir(parents=True, exist_ok=True)
        for a in artifacts:
            if a.is_dir():
                for child in a.glob("*.txt"):
                    shutil.copy2(child, m / child.name)
            else:
                shutil.copy2(a, m / a.name)

def main() -> int:
    ap = argparse.ArgumentParser(description="Piper cleanup inventory (TXT outputs)")
    ap.add_argument("--root",   default=r"C:\Piper\scripts", help="Root to scan (default: C:\\Piper\\scripts)")
    ap.add_argument("--out",    default=r"C:\Piper\run",     help="Output dir (default: C:\\Piper\\run)")
    ap.add_argument("--mirror", nargs="*", default=[],       help="Optional mirror dirs to copy results into")
    args = ap.parse_args()

    root = Path(args.root)
    out  = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    py_files = _collect_py_files(root)
    if not py_files:
        print(f"[ERR] No .py files under {root}", file=sys.stderr)
        return 2

    sizes_txt = out / "CLEAN_sizes.txt"
    map_txt   = out / "CLEAN_map_by_symbol.txt"
    ctx_dir   = out / "CLEAN_ctx"

    print(f"[+] Scanning {len(py_files)} Python files under {root}")
    snapshot_sizes(py_files, sizes_txt);  print(f"[+] Wrote {sizes_txt}")
    build_symbol_map(py_files, map_txt);  print(f"[+] Wrote {map_txt}")
    dump_contexts(py_files, ctx_dir, 5);  print(f"[+] Wrote contexts in {ctx_dir}")

    mirrors = [Path(m) for m in args.mirror]
    if mirrors:
        copy_to_mirrors([sizes_txt, map_txt, ctx_dir], mirrors)
        for m in mirrors:
            print(f"[+] Mirrored artifacts to: {m}")

    print("[OK] Inventory complete.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

