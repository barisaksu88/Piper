# C:\Piper\tools\make_sourcepack.py
# Creates a small "Source Pack" of your current scripts for a new thread:
# - Zips only .py/.txt/.md under C:\Piper\scripts (or --root)
# - Skips caches/venv/snapshots/run/etc.
# - Writes a manifest with path, size, line count, SHA256
# - Outputs to C:\Piper\run\sourcepacks (or --outdir)

import argparse
import hashlib
import os
import sys
import time
import zipfile
from pathlib import Path

DEFAULT_ROOT = r"C:\Piper\scripts"
DEFAULT_OUTDIR = r"C:\Piper\run\sourcepacks"
INCLUDE_EXTS = {".py", ".txt", ".md"}
SKIP_DIR_NAMES = {
    "__pycache__", ".git", ".idea", ".vscode",
    "venv", ".venv", "env", ".env",
    "snapshots", "run", "logs", "dist", "build",
}
# Optional: skip top-level siblings (safety if root isn't exact)
TOPLEVEL_SKIP = {"venv", ".venv", "snapshots", "run", "logs"}

def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def line_count_of_file(path: Path) -> int:
    try:
        with path.open("rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return -1  # if binary/encoding issues, still include

def should_skip_dir(dir_name: str) -> bool:
    name = dir_name.strip()
    return name in SKIP_DIR_NAMES

def collect_files(root: Path):
    # Walk root and gather include-extensions, skipping unwanted dirs
    files = []
    for current_dir, dirnames, filenames in os.walk(root):
        # Prune dirs in place for efficiency
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        # Prevent accidentally packing siblings (when root is too high)
        if root == root.anchor:
            if any(part in TOPLEVEL_SKIP for part in Path(current_dir).parts):
                continue
        for fn in filenames:
            p = Path(current_dir) / fn
            if p.suffix.lower() in INCLUDE_EXTS:
                files.append(p)
    return files

def main():
    ap = argparse.ArgumentParser(description="Create Source Pack (zip + manifest) for Piper scripts.")
    ap.add_argument("--root", default=DEFAULT_ROOT, help=f"Root of scripts (default: {DEFAULT_ROOT})")
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR, help=f"Output directory (default: {DEFAULT_OUTDIR})")
    ap.add_argument("--label", default="", help="Optional label to append to filenames (e.g., PV12 or RERAIL2)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    if not root.exists() or not root.is_dir():
        print(f"[ERROR] Root does not exist or is not a directory: {root}", file=sys.stderr)
        sys.exit(2)

    stamp = time.strftime("%Y%m%d_%H%M")
    base = f"SourcePack_{stamp}"
    if args.label:
        base += f"_{args.label}"

    zip_path = outdir / f"{base}.zip"
    manifest_path = outdir / f"SourceManifest_{stamp}{('_' + args.label) if args.label else ''}.txt"

    files = collect_files(root)
    rel_paths = [f.relative_to(root) for f in files]

    # Write zip
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p, rel in zip(files, rel_paths):
            z.write(p, arcname=str(rel))

    # Compute manifest
    rows = []
    total_bytes = 0
    for p, rel in zip(files, rel_paths):
        try:
            sz = p.stat().st_size
        except Exception:
            sz = -1
        total_bytes += max(0, sz)
        rows.append({
            "rel": str(rel).replace("\\", "/"),
            "size": sz,
            "lines": line_count_of_file(p),
            "sha256": sha256_of_file(p),
        })

    # Write manifest (human-friendly)
    header = (
        "====================================================\n"
        "Piper Source Manifest\n"
        f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Root: {root}\n"
        f"Pack: {zip_path}\n"
        f"Files: {len(rows)}\n"
        f"Total Size: {total_bytes} bytes\n"
        "====================================================\n\n"
        "relpath | size | lines | sha256\n"
        "--------------------------------\n"
    )
    with manifest_path.open("w", encoding="utf-8") as f:
        f.write(header)
        for r in rows:
            f.write(f"{r['rel']} | {r['size']} | {r['lines']} | {r['sha256']}\n")

    print("[SOURCEPACK] Wrote:")
    print(f"  ZIP      : {zip_path}")
    print(f"  MANIFEST : {manifest_path}")
    print(f"  Files    : {len(rows)} (~{total_bytes} bytes)")

if __name__ == "__main__":
    main()
