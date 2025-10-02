# scripts/make_snapshot.py
from __future__ import annotations
import zipfile
from pathlib import Path
from datetime import datetime

def include(p: Path) -> bool:
    # Skip caches and runtime artifacts
    if any(part == "__pycache__" for part in p.parts):
        return False
    # Allow .py and .txt always; allow other files only under tests/
    if p.suffix in {".py", ".txt"}:
        return True
    if "tests" in p.parts:
        return True
    return False

def main():
    root = Path(__file__).resolve().parents[1]   # -> C:\Piper
    scripts_dir = root / "scripts"
    snapshots = root / "snapshots"
    snapshots.mkdir(exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = snapshots / f"{stamp}_core_baseline.zip"

    # Track relpaths we've written to avoid duplicates
    seen: set[Path] = set()

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # Walk ONLY scripts/ to avoid double-adding tests/
        for p in scripts_dir.rglob("*"):
            if not p.is_file():
                continue
            if not include(p):
                continue
            rel = p.relative_to(root)
            if rel in seen:
                continue
            seen.add(rel)
            z.write(p, rel)

    print(f"[SNAPSHOT] created {out} (files={len(seen)})")

if __name__ == "__main__":
    main()

