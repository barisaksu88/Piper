"""
mirror_watcher.py - Watches C:/Projects/Piper and mirrors .py/.md/.txt changes
to C:/Users/Hawk Gaming/PiperMirror/, preserving folder structure.
Run with: python mirror_watcher.py
"""

import sys
import time
import shutil
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

SRC = Path(r"C:\Projects\Piper")
DST = Path(r"C:\Users\Hawk Gaming\PiperMirror")

MIRROR_EXTENSIONS = {".py", ".md", ".txt", ".log"}

IGNORE_DIRS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".venv", "venv"}
IGNORE_FILES = {"mirror_watcher.log"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).resolve().parent / "mirror_watcher.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("mirror")


def _should_mirror(path: Path) -> bool:
    if path.suffix.lower() not in MIRROR_EXTENSIONS:
        return False
    rel = path.relative_to(SRC)
    if rel.name in IGNORE_FILES:
        return False
    for part in rel.parts[:-1]:
        if part in IGNORE_DIRS:
            return False
    return True


def _mirror_path(src_path: Path) -> Path:
    return DST / src_path.relative_to(SRC)


def _copy(src_path: Path):
    try:
        is_file = src_path.is_file()
    except OSError:
        return  # file locked (e.g. chroma.sqlite3-journal) — skip silently
    if not is_file:
        return
    if not _should_mirror(src_path):
        return
    dst_path = _mirror_path(src_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src_path, dst_path)
    except OSError:
        return  # locked during copy — skip silently
    log.info("COPY   %s", src_path.relative_to(SRC))


def _delete(src_path: Path):
    dst_path = _mirror_path(src_path)
    if dst_path.is_file():
        dst_path.unlink(missing_ok=True)
        log.info("DELETE %s", src_path.relative_to(SRC))
    elif dst_path.is_dir():
        shutil.rmtree(dst_path, ignore_errors=True)
        log.info("RMDIR  %s", src_path.relative_to(SRC))


class MirrorHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if not event.is_directory:
            _copy(Path(event.src_path))

    def on_created(self, event):
        if not event.is_directory:
            _copy(Path(event.src_path))

    def on_deleted(self, event):
        p = Path(event.src_path)
        if _should_mirror(p) or not p.suffix:
            _delete(p)

    def on_moved(self, event):
        _delete(Path(event.src_path))
        _copy(Path(event.dest_path))


def _src_path(dst_path: Path) -> Path:
    return SRC / dst_path.relative_to(DST)


def mirror_cleanup():
    """Remove mirror files that no longer exist in source or shouldn't be mirrored."""
    if not DST.exists():
        return
    removed = 0
    for dst_file in list(DST.rglob("*")):
        if not dst_file.is_file():
            continue
        src_file = _src_path(dst_file)
        if not src_file.exists() or not _should_mirror(src_file):
            dst_file.unlink(missing_ok=True)
            log.info("PURGE  %s", dst_file.relative_to(DST))
            removed += 1
    # Remove empty directories left behind
    for dst_dir in sorted(DST.rglob("*"), reverse=True):
        if dst_dir.is_dir() and not any(dst_dir.iterdir()):
            dst_dir.rmdir()
    if removed:
        log.info("Cleanup removed %d stale files", removed)


def initial_sync():
    log.info("Initial sync: %s -> %s", SRC, DST)
    mirror_cleanup()
    count = 0
    for src_file in SRC.rglob("*"):
        if src_file.is_file() and _should_mirror(src_file):
            _copy(src_file)
            count += 1
    log.info("Initial sync complete -- %d files mirrored", count)


def main():
    DST.mkdir(parents=True, exist_ok=True)
    initial_sync()

    handler = MirrorHandler()
    observer = Observer()
    observer.schedule(handler, str(SRC), recursive=True)
    observer.start()
    log.info("Watching %s  (Ctrl+C to stop)", SRC)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        log.info("Watcher stopped.")


if __name__ == "__main__":
    main()
