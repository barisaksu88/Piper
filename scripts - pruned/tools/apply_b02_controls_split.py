import os, re, sys
from pathlib import Path

ROOT = Path(r"C:\Piper\scripts")
panes_path = ROOT / "ui" / "panes.py"

if not panes_path.exists():
    print(f"[B02] ERROR: {panes_path} not found")
    sys.exit(1)

src = panes_path.read_text(encoding="utf-8")

changed = False

# 1) Ensure import of the component
import_line = "from ui.components.controls_pane import build as dev_controls_build, refresh as dev_controls_refresh  # B02"
if import_line not in src:
    # Insert after the last 'from ui.' import near the top
    m = list(re.finditer(r"^from ui\.[^\n]+\n", src, flags=re.MULTILINE))
    if m:
        idx = m[-1].end()
        src = src[:idx] + import_line + "\n" + src[idx:]
    else:
        # Fallback: after first import block
        m2 = re.search(r"^import[^\n]+\n", src, flags=re.MULTILINE)
        pos = m2.end() if m2 else 0
        src = src[:pos] + import_line + "\n" + src[pos:]
    changed = True

# 2) Add a small wrapper function (if not already present)
wrapper_code = r"""
def _mount_dev_controls_if_enabled(parent_container_id, adapters=None):
    \"\"\"B02: flag-gated dev controls; renders only when PIPER_UI_DEV_INPUT is truthy.\"\"\"
    import os
    flag = os.getenv("PIPER_UI_DEV_INPUT", "")
    if not flag or flag == "0":
        return None
    dev_controls_build(parent=parent_container_id, adapters=adapters or {})
    return True
"""
if "_mount_dev_controls_if_enabled(" not in src:
    # Append near end (safe, no behavior drift)
    src = src.rstrip() + "\n" + wrapper_code + "\n"
    changed = True

# 3) Insert a mount call immediately AFTER the logs pane is built.
#   We try several common call shapes to capture the parent=<X> and reuse it.

patterns = [
    # e.g., build_logs_pane(parent=right_col)
    r"(?P<call>(?P<name>build_?logs_?pane)\s*\(\s*parent\s*=\s*(?P<parent>[^),]+)[^\)]*\)\s*)",
    # e.g., logs_pane.build(parent=right_col)
    r"(?P<call>logs_?pane\.(?:build|render)\s*\(\s*parent\s*=\s*(?P<parent>[^),]+)[^\)]*\)\s*)",
    # e.g., build_logs(parent=right_col)
    r"(?P<call>build_?logs\s*\(\s*parent\s*=\s*(?P<parent>[^),]+)[^\)]*\)\s*)",
]

inserted = False
for pat in patterns:
    for m in re.finditer(pat, src):
        call = m.group("call")
        parent = m.group("parent").strip()
        inject = f"{call}\n_mount_dev_controls_if_enabled(parent_container_id={parent})\n"
        # Only insert once (after the first logs mount)
        src = src.replace(call, inject, 1)
        inserted = True
        changed = True
        break
    if inserted:
        break

if not inserted:
    print("[B02] WARNING: could not auto-locate logs pane call; Dev controls not mounted. Import + wrapper added.")
else:
    print("[B02] OK: Dev controls mounted after logs pane.")

if changed:
    panes_path.write_text(src, encoding="utf-8")
    print(f"[B02] Patched: {panes_path}")
else:
    print("[B02] No changes needed (already patched).")

