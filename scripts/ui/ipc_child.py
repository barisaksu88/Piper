# C:\Piper\scripts\ui\ipc_child.py
from __future__ import annotations

import sys
import os
import threading
import subprocess
from pathlib import Path
from typing import Callable, Optional

OnLine = Optional[Callable[[str], None]]

class _Child:
    def __init__(self, proc: subprocess.Popen):
        self._proc = proc

    def write(self, text: str) -> bool:
        """Send one line to the child stdin. Returns False if process is dead."""
        try:
            if self._proc.poll() is not None:
                return False
            if self._proc.stdin is None:
                return False
            # ensure newline-terminated
            line = (text or "").rstrip("\r\n") + "\n"
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
            return True
        except Exception:
            return False

    def stop(self) -> None:
        try:
            if self._proc.poll() is None:
                try:
                    if self._proc.stdin:
                        # be nice; some CLIs exit on EOF
                        self._proc.stdin.close()
                except Exception:
                    pass
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                try:
                    self._proc.wait(timeout=1.5)
                except Exception:
                    pass
        except Exception:
            pass


def _project_root() -> Path:
    """
    Heuristic: find the folder that contains the 'scripts' package with entries/.
    This file lives at <root>/scripts/ui/ipc_child.py
    """
    here = Path(__file__).resolve()
    for p in [here.parent, here.parent.parent, here.parent.parent.parent, here.parent.parent.parent.parent]:
        try:
            if (p / "scripts" / "entries").is_dir():
                return p
        except Exception:
            pass
    # Fallback: two levels up (/scripts/..)
    return here.parents[2]


def _resolve_cli_module() -> Optional[str]:
    try:
        import importlib.util as iu
        return "entries.app_cli_entry" if iu.find_spec("entries.app_cli_entry") else None
    except Exception:
        return None

def _pump(pipe, on_line: OnLine, is_err: bool = False):
    """Read a pipe line-by-line and forward to on_line."""
    try:
        if pipe is None:
            return
        for raw in iter(pipe.readline, ""):
            if not raw:
                break
            line = raw.rstrip("\r\n")
            if not line:
                continue
            if on_line:
                # Light hint for stderr so your badge normalizer can catch it
                on_line(("[ERR] " + line) if is_err else line)
    except Exception:
        pass
    finally:
        try:
            if pipe:
                pipe.close()
        except Exception:
            pass

def spawn_cli(on_line: OnLine = None) -> _Child:
    """
    Start the dev CLI child and return a handle with write()/stop().
    Auto-detects the correct module path and sets cwd to the project root
    so `python -m scripts.entries.app_cli_entry` works reliably.
    """
    root = _project_root()
    mod = _resolve_cli_module()
    if not mod:
        raise RuntimeError("Could not locate app_cli_entry module (checked scripts.entries and entries).")

    cmd = [sys.executable, "-u", "-m", mod]

    # Environment passes through; cwd is project root to stabilize module discovery
    proc = subprocess.Popen(
        cmd,
        cwd=str(root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    # Wire readers
    t_out = threading.Thread(target=_pump, args=(proc.stdout, on_line, False), daemon=True)
    t_err = threading.Thread(target=_pump, args=(proc.stderr, on_line, True), daemon=True)
    t_out.start()
    t_err.start()

    # Optional: emit a one-line dev trace so you can see what we launched
    try:
        if on_line:
            on_line(f"[DEV][TRACE] spawn_cli: cwd={root} mod={mod}")
    except Exception:
        pass

    return _Child(proc)

