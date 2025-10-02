# collate_focus_ui.py — small, Canvas-friendly bundle (UI + entrypoint only)
# Reads: deps.txt, vulture.txt, bandit.txt
# Writes: analyzer_focus_ui.md

import pathlib, re, datetime

ROOT = pathlib.Path(r"C:\Piper")
SIDE = ROOT / "sidekick"
OUT  = SIDE / "analyzer_focus_ui.md"

def read(p): 
    p = SIDE / p
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""

deps = read("deps.txt").splitlines()
vult = read("vulture.txt").splitlines()
band = read("bandit.txt").splitlines()

def keep_ui_edges(lines):
    out = []
    for ln in lines:
        if "->" not in ln: 
            continue
        lhs, rhs = [x.strip() for x in ln.split("->", 1)]
        if lhs.startswith("scripts.ui.") or rhs.startswith("scripts.ui.") or \
           lhs.startswith("scripts.entries.app_gui_entry") or rhs.startswith("scripts.entries.app_gui_entry"):
            out.append(f"{lhs} -> {rhs}")
    # de-dup and shorten long module names with middle elision
    seen, res = set(), []
    for e in out:
        if e in seen: 
            continue
        seen.add(e)
        res.append(e)
    return res[:2000]  # hard cap

def keep_ui_vulture(lines):
    out = []
    for ln in lines:
        m = re.match(r'(?P<file>.*scripts[\\/].*?):(?P<lineno>\d+):\s*(?P<msg>.+)', ln)
        if not m: 
            continue
        f = m.group("file").replace("\\", "/")
        if "/ui/" in f or f.endswith("entries/app_gui_entry.py"):
            out.append(ln)
    return out[:3000]  # plenty but bounded

def keep_ui_bandit(lines):
    # take only blocks that mention ui/ or app_gui_entry.py
    txt = "\n".join(lines)
    blocks = txt.split("\n\n")
    kept = []
    for b in blocks:
        if "scripts/ui/" in b or "scripts\\ui\\" in b or "entries/app_gui_entry.py" in b:
            kept.append(b.strip())
    return "\n\n".join(kept[:40])  # keep up to 40 blocks

ui_edges  = keep_ui_edges(deps)
ui_vuln   = keep_ui_vulture(vult)
ui_bandit = keep_ui_bandit(band)

now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
parts = []
parts.append("# Piper — UI-focused bundle (Canvas-friendly)\n")
parts.append(f"_Generated: {now}_\n\n---\n")

parts.append("## DEPS (only edges touching UI or app_gui_entry)\n\n```\n")
parts += [e + "\n" for e in ui_edges]
parts.append("```\n\n")

parts.append("## VULTURE (only findings under scripts/ui or app_gui_entry)\n\n```\n")
parts += [ln + "\n" for ln in ui_vuln]
parts.append("```\n\n")

parts.append("## BANDIT (only findings mentioning UI/app_gui_entry)\n\n```\n")
parts.append(ui_bandit + ("\n" if ui_bandit else ""))
parts.append("```\n")

OUT.write_text("".join(parts), encoding="utf-8")
print("Wrote", OUT)
