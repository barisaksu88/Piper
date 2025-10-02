# C:\Piper\scripts\ui\tailer.py
# LL03: Windows-friendly tailer
# - Opens the file with FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE (no writer lock)
# - Resilient to rotations/truncation
# - Simple polling (interval provided by caller)

from __future__ import annotations
import os
import sys
import time
import threading
from pathlib import Path
from typing import Callable, Optional

_ON_WINDOWS = os.name == "nt"

if _ON_WINDOWS:
    import msvcrt
    import ctypes
    from ctypes import wintypes

    # Win32 constants
    GENERIC_READ  = 0x80000000
    FILE_SHARE_READ   = 0x00000001
    FILE_SHARE_WRITE  = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80

    _CreateFileW = ctypes.windll.kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE
    ]
    _CreateFileW.restype = wintypes.HANDLE

    _CloseHandle = ctypes.windll.kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL


class Tailer:
    """
    Poll-based log tailer that does NOT lock the file for writers.
    Callbacks:
      - on_line(str): for each full line
      - on_status(str): for status/info
      - on_error(str): for errors
    """

    def __init__(self, path: Path, from_start: bool = False, poll_interval: float = 0.25):
        self.path = Path(path)
        self.from_start = from_start
        self.poll_interval = max(0.05, float(poll_interval))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._f = None  # type: ignore
        self._win_handle = None  # type: ignore

    # ---------- low-level open/close with sharing on Windows ----------
    def _open_shared(self):
        if not self.path.exists():
            # Ensure the parent exists; create empty file for tail
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.touch(exist_ok=True)
            except Exception:
                pass

        if _ON_WINDOWS:
            # CreateFileW with read + shared RW delete
            h = _CreateFileW(
                str(self.path),
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None
            )
            if h == wintypes.HANDLE(-1).value or h == 0:
                raise OSError(f"CreateFileW failed for {self.path}")
            fd = msvcrt.open_osfhandle(int(h), os.O_RDONLY)
            f = os.fdopen(fd, "r", encoding="utf-8", errors="replace", newline="")
            self._win_handle = h
            self._f = f
        else:
            # POSIX default allows shared read
            self._f = open(self.path, "r", encoding="utf-8", errors="replace", newline="")
        return self._f

    def _close_current(self):
        try:
            if self._f:
                self._f.close()
        finally:
            self._f = None
            if _ON_WINDOWS and self._win_handle:
                try:
                    _CloseHandle(self._win_handle)
                except Exception:
                    pass
                self._win_handle = None

    # ---------- thread loop ----------
    def start_in_thread(
        self,
        on_line: Callable[[str], None],
        on_status: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        def _status(msg: str):
            if on_status:
                try:
                    on_status(msg)
                except Exception:
                    pass

        def _error(msg: str):
            if on_error:
                try:
                    on_error(msg)
                except Exception:
                    pass

        def _tail_loop():
            buf = ""
            last_ino = None
            first_open = True

            while not self._stop.is_set():
                try:
                    if self._f is None:
                        self._open_shared()
                        # track inode (or fallback to stat signature)
                        st = self.path.stat()
                        last_ino = getattr(st, "st_ino", (st.st_dev, st.st_ino) if hasattr(st, "st_ino") else (st.st_size, st.st_mtime))
                        _status(f"[Tail] Opened: {self.path}")

                        # seek
                        if self.from_start and first_open:
                            self._f.seek(0, os.SEEK_SET)
                        else:
                            self._f.seek(0, os.SEEK_END)
                        first_open = False

                    # detect rotation/truncate
                    try:
                        st = self.path.stat()
                        sig = getattr(st, "st_ino", (st.st_dev, st.st_ino) if hasattr(st, "st_ino") else (st.st_size, st.st_mtime))
                        if sig != last_ino:
                            # file replaced/rotated
                            self._close_current()
                            _status(f"[Tail] Reopening (rotation detected): {self.path}")
                            continue
                    except FileNotFoundError:
                        # rotated away; wait for reappearance
                        self._close_current()
                        time.sleep(self.poll_interval)
                        continue

                    # read chunk
                    chunk = self._f.read()
                    if chunk:
                        buf += chunk
                        # emit lines
                        while True:
                            nl = buf.find("\n")
                            if nl == -1:
                                break
                            line = buf[:nl + 1]
                            buf = buf[nl + 1:]
                            try:
                                on_line(line)
                            except Exception:
                                pass
                    else:
                        time.sleep(self.poll_interval)
                except Exception as e:
                    _error(f"[Tail] Error: {e!r}")
                    self._close_current()
                    time.sleep(self.poll_interval)

            # flush remaining partial
            if buf:
                try:
                    on_line(buf)
                except Exception:
                    pass
            self._close_current()
            _status("[Tail] Stopped.")

        self._stop.clear()
        self._thread = threading.Thread(target=_tail_loop, name="TailerThread", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.5):
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None
        self._close_current()

