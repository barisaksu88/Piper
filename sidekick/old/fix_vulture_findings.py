# fix_vulture_findings.py
# Idempotent fixer for a few specific Vulture hits you saw.
# - persona_adapter.py: remove 'copy' and 'importlib' from combined import lines
# - tts_manager.py: remove top-level 'voice = ...' assignment
# - _panes_impl.py: drop import lines for controls_build/controls_refresh/calibrate_to_viewport
# - (optional) add "# noqa: F841" to obvious throwaways (stick_to_bottom, u)

from pathlib import Path
import re

def save_backup(p: Path, suffix: str):
    b = p.with_suffix(p.suffix + suffix)
    b.write_text(p.read_text(encoding="utf-8-sig"), encoding="utf-8")

def persona_adapter_fix(p: Path):
    if not p.exists(): return
    save_backup(p, ".bak_imports2")
    txt = p.read_text(encoding="utf-8-sig")
    out = []
    for ln in txt.splitlines(True):
        m = re.match(r'^(\s*)import\s+(.+?)\s*(#.*)?\r?$', ln)
        if m:
            indent, names, comment = m.group(1), m.group(2), m.group(3) or ""
            # split by commas, trim, drop target names
            parts = [x.strip() for x in names.split(",")]
            parts = [x for x in parts if x not in {"copy", "importlib"}]
            if parts:
                ln = f"{indent}import {', '.join(parts)}{(' ' + comment if comment else '')}\n"
            else:
                ln = ""  # whole line becomes empty if nothing left
        out.append(ln)
    p.write_text("".join(out), encoding="utf-8")

def tts_manager_fix(p: Path):
    if not p.exists(): return
    save_backup(p, ".bak_voice2")
    txt = p.read_text(encoding="utf-8-sig")
    # remove ONLY a top-level assignment/annotation to 'voice'
    txt = re.sub(r'(?m)^[ \t]*voice[ \t]*[:=].*\r?\n', '', txt)
    p.write_text(txt, encoding="utf-8")

def panes_impl_fix(p: Path):
    if not p.exists(): return
    save_backup(p, ".bak_unused_imports2")
    txt = p.read_text(encoding="utf-8-sig")
    # drop import lines that pull in the flagged names
    patterns = [
        r'(?m)^[ \t]*from[ \t].*import[ \t].*\bcontrols_build\b.*\r?\n',
        r'(?m)^[ \t]*from[ \t].*import[ \t].*\bcontrols_refresh\b.*\r?\n',
        r'(?m)^[ \t]*from[ \t].*import[ \t].*\bcalibrate_to_viewport\b.*\r?\n',
    ]
    for pat in patterns:
        txt = re.sub(pat, '', txt)
    p.write_text(txt, encoding="utf-8")

def optional_silence_noqa(p: Path):
    # Silences obvious throwaways if present
    if not p.exists(): return
    save_backup(p, ".bak_noqa2")
    txt = p.read_text(encoding="utf-8-sig")

    # add F841 marker to simple assignments for stick_to_bottom / u
    def add_noqa(line, name):
        if re.search(rf'^\s*{name}\s*=.*$', line) and '# noqa' not in line:
            return line.rstrip() + '  # noqa: F841\n'
        return line

    out = []
    for ln in txt.splitlines(True):
        if re.match(r'^\s*stick_to_bottom\s*=.*$', ln):
            ln = add_noqa(ln, 'stick_to_bottom')
        elif re.match(r'^\s*u\s*=.*$', ln):
            ln = add_noqa(ln, 'u')
        out.append(ln)
    p.write_text("".join(out), encoding="utf-8")

def main():
    root = Path(r"C:\Piper\scripts")

    persona_adapter_fix(root / "services" / "persona_adapter.py")
    tts_manager_fix(root / "services" / "tts" / "tts_manager.py")
    panes_impl_fix(root / "ui" / "_panes_impl.py")

    # Optional: uncomment if you want to silence these now
    optional_silence_noqa(root / "ui" / "components" / "chat_pane.py")
    optional_silence_noqa(root / "ui" / "dev_tools.py")

if __name__ == "__main__":
    main()
