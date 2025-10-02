# scripts/tests/size_caps.py
from __future__ import annotations
import os
import sys
from pathlib import Path

APP_CAP   = 150
PANES_CAP = 120

def count_lines(p: Path) -> int:
    try:
        with p.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception as e:
        print(f"[SIZE][ERR] cannot open {p}: {e}")
        return -1

def main() -> int:
    here = Path(__file__).resolve()
    scripts_dir = here.parent.parent  # .../scripts
    app_gui = scripts_dir / "entries" / "app_gui_entry.py"
    panes   = scripts_dir / "ui" / "panes.py"

    app_n   = count_lines(app_gui)
    panes_n = count_lines(panes)

    strict = os.environ.get("PIPER_SIZE_STRICT", "0") == "1"
    exit_code = 0

    def report(name: str, n: int, cap: int):
        nonlocal exit_code
        if n < 0:
            print(f"[SIZE][MISS] {name}: file not found")
            if strict:
                exit_code = 1
            return
        status = "SIZE OK" if n <= cap else "WARN"
        if strict and n > cap:
            status = "FAIL"
            exit_code = 1
        print(f"[SIZE] {name} = {n} lines (cap={cap}): {status}")

    report("entries/app_gui_entry.py", app_n, APP_CAP)
    report("ui/panes.py",            panes_n, PANES_CAP)

    return exit_code

if __name__ == "__main__":
    sys.exit(main())

