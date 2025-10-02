# C:\Piper\scripts\tools\b04_splitter.py
"""
B04 splitter â€” behavior-preserving size trim for:
  - entries/app_gui_entry.py  -> entries/_app_gui_entry_impl.py + thin wrapper
  - ui/panes.py               -> ui/_panes_impl.py + thin wrapper

It moves the full original source into *_impl.py once, then writes wrappers
that import and re-export the public API. Zero behavior drift.

Safe to re-run: no-op if impl files already exist.
"""

from __future__ import annotations
import io, os, sys
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).resolve().parents[1]  # C:\Piper\scripts

def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def _write(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8", newline="\n")

def _move_to_impl(src: Path, impl: Path, wrapper_text: str) -> Tuple[bool, str]:
    """
    If impl exists, we assume we've already split; leave src as-is (but we can refresh wrapper if needed).
    If impl does NOT exist:
      - rename src -> impl (full body)
      - write wrapper to src path
    Returns (changed, message).
    """
    if impl.exists():
        # Ensure wrapper exists and is small; if src is still large, re-write wrapper.
        try:
            cur = _read(src)
        except Exception:
            cur = ""
        if len(cur.splitlines()) > 200 or "AUTO-GENERATED: B04 thin wrapper" not in cur:
            _write(src, wrapper_text)
            return True, f"Refreshed wrapper for {src}"
        return False, f"No change: {impl.name} already present"
    # First-time split
    body = _read(src)
    _write(impl, body)
    _write(src, wrapper_text)
    return True, f"Moved {src.name} -> {impl.name} and wrote thin wrapper"

def main():
    # 1) entries/app_gui_entry.py
    src_app = ROOT / "entries" / "app_gui_entry.py"
    impl_app = ROOT / "entries" / "_app_gui_entry_impl.py"
    wrapper_app = """# AUTO-GENERATED: B04 thin wrapper for entries/app_gui_entry.py
from __future__ import annotations

# Import the full implementation kept in this private module.
from ._app_gui_entry_impl import run as run

# Re-export run() as the module's main entry point. Keep a CLI-friendly main.
if __name__ == "__main__":
    run()
"""
    # 2) ui/panes.py
    src_panes = ROOT / "ui" / "panes.py"
    impl_panes = ROOT / "ui" / "_panes_impl.py"
    wrapper_panes = """# AUTO-GENERATED: B04 thin wrapper for ui/panes.py
from __future__ import annotations

# Import the full implementation from the private module.
from ._panes_impl import (
    init_ui,
    refresh_ui,
    calibrate_to_viewport,
)

__all__ = ["init_ui", "refresh_ui", "calibrate_to_viewport"]
"""

    results = []
    for (src, impl, wrap) in [
        (src_app, impl_app, wrapper_app),
        (src_panes, impl_panes, wrapper_panes),
    ]:
        if not src.exists():
            results.append(f"SKIP: {src} (not found)")
            continue
        changed, msg = _move_to_impl(src, impl, wrap)
        results.append(msg)

    print("\n".join(results))

if __name__ == "__main__":
    main()

