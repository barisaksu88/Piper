# collate_focus_patterns.py — pick findings that match any include pattern(s)
# Usage examples:
#   py collate_focus_patterns.py --include scripts.core. entries/app_gui_entry.py
#   py collate_focus_patterns.py --include scripts.ui. scripts.entries.app_gui_entry

import argparse, pathlib, re, datetime

ROOT = pathlib.Path(r"C:\Piper")
SIDE = ROOT / "sidekick"
OUT  = SIDE / "analyzer_focus.md"

def read(p): 
    p = SIDE / p
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""

def any_hit(text: str, pats) -> bool:
    return any(p in text for p in pats)

def keep_deps(lines, pats, cap=4000):
    out = []
    for ln in lines:
        if "->" not in ln: 
            continue
        if any_hit(ln, pats):
            out.append(ln.strip())
            if len(out) >= cap: break
    return out

def keep_vulture(lines, pats, cap=6000):
    out = []
    for ln in lines:
        if any_hit(ln, pats):
            out.append(ln.rstrip())
            if len(out) >= cap: break
    return out

def keep_bandit(text, pats, cap_blocks=60):
    blocks = text.split("\n\n")
    kept = []
    for b in blocks:
        if any_hit(b, pats):
            kept.append(b.strip())
            if len(kept) >= cap_blocks: break
    return "\n\n".join(kept)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include", nargs="+", required=True, help="substr patterns to keep (e.g., scripts.core. entries/app_gui_entry.py)")
    ap.add_argument("--out", default=str(OUT), help="output md path")
    args = ap.parse_args()
    pats = args.include

    deps = read("deps.txt").splitlines()
    vult = read("vulture.txt").splitlines()
    band = read("bandit.txt")

    ui_edges  = keep_deps(deps, pats)
    ui_vult   = keep_vulture(vult, pats)
    ui_bandit = keep_bandit(band, pats)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = []
    parts.append("# Piper — Focus bundle (Canvas-friendly)\n")
    parts.append(f"_Generated: {now}_\n\n")
    parts.append(f"**Patterns:** {', '.join(pats)}\n\n---\n")

    parts.append("## DEPS (matching patterns)\n\n```\n")
    parts += [(e + "\n") for e in ui_edges]
    parts.append("```\n\n")

    parts.append("## VULTURE (matching patterns)\n\n```\n")
    parts += [(ln + "\n") for ln in ui_vult]
    parts.append("```\n\n")

    parts.append("## BANDIT (matching patterns)\n\n```\n")
    parts.append((ui_bandit + "\n") if ui_bandit else "")
    parts.append("```\n")

    outp = pathlib.Path(args.out)
    outp.write_text("".join(parts), encoding="utf-8")
    print("Wrote", outp)

if __name__ == "__main__":
    main()
