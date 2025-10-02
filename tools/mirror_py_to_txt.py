# mirror_py_to_txt.py (tiny tweak: add --dest)
from __future__ import annotations
import os, time
from pathlib import Path
import argparse

SRC_ROOT = Path(r"C:\Piper\scripts")
INCLUDE_EXT = ".py"
DEST_EXT = ".txt"
POLL_SECS = 1.0
EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__", "build", "dist", "models", "runtime"}

def relpaths(root: Path):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in EXCLUDE_DIRS]
        for fn in fns:
            if fn.lower().endswith(INCLUDE_EXT):
                yield Path(dp, fn).relative_to(root)

def mirror_once(src_root: Path, dest_root: Path):
    for rel in relpaths(src_root):
        src = src_root / rel
        dest = (dest_root / rel).with_suffix(DEST_EXT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if (not dest.exists()
                or src.stat().st_mtime > dest.stat().st_mtime
                or src.stat().st_size != dest.stat().st_size):
                with open(src, "r", encoding="utf-8", errors="replace") as f:
                    data = f.read()
                with open(dest, "w", encoding="utf-8", newline="") as f:
                    f.write(data)
                print(f"[MIRROR] wrote {dest.relative_to(dest_root)}")
        except FileNotFoundError:
            pass

    # cleanup: remove orphan .txt
    for dp, dns, fns in os.walk(dest_root):
        dns[:] = [d for d in dns if d not in EXCLUDE_DIRS]
        for fn in fns:
            if fn.lower().endswith(DEST_EXT):
                dest = Path(dp, fn)
                rel = dest.relative_to(dest_root).with_suffix(INCLUDE_EXT)
                if not (src_root / rel).exists():
                    dest.unlink(missing_ok=True)
                    print(f"[CLEAN] removed {dest.relative_to(dest_root)}")

def try_watchdog(src_root: Path, dest_root: Path):
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except Exception:
        return False
    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            mirror_once(src_root, dest_root)
    obs = Observer()
    obs.schedule(Handler(), str(src_root), recursive=True)
    obs.start()
    print("[WATCH] Using watchdog (event-driven). Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True, help="Destination root for TXT mirror (Drive-synced)")
    ap.add_argument("--src", default=str(SRC_ROOT), help="Source root (default C:\\Piper\\scripts)")
    args = ap.parse_args()

    src_root = Path(args.src).resolve()
    dest_root = Path(args.dest).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    print(f"[START] Mirroring {src_root} -> {dest_root} (*.py -> *.txt)")
    mirror_once(src_root, dest_root)

    if try_watchdog(src_root, dest_root):
        return

    print("[POLL] watchdog not found; using 1s polling.")
    try:
        while True:
            mirror_once(src_root, dest_root)
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        print("\n[STOP] Mirror stopped.")

if __name__ == "__main__":
    main()
