# collate_merge_all.py — zero-filter concatenation of analyzer outputs to a single Markdown file
# Inputs (if present):
#   C:\Piper\sidekick\deps.txt
#   C:\Piper\sidekick\vulture.txt
#   C:\Piper\sidekick\bandit.txt
# Output:
#   C:\Piper\sidekick\analyzer_full.md

import pathlib
import datetime

ROOT = pathlib.Path(r"C:\Piper")
SIDE = ROOT / "sidekick"
OUT = SIDE / "analyzer_full.md"

SECTIONS = [
    ("DEPS (import edges)", SIDE / "deps.txt"),
    ("VULTURE (unused/dead code)", SIDE / "vulture.txt"),
    ("BANDIT (security findings)", SIDE / "bandit.txt"),
]

def read_text(p: pathlib.Path) -> str:
    if not p.exists():
        return "(file not found)\n"
    return p.read_text(encoding="utf-8", errors="ignore")

now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
lines = []
lines.append("# Piper — Full Analyzer Bundle (no filter)\n")
lines.append(f"_Generated: {now}_\n")
lines.append("\n---\n")

for title, path in SECTIONS:
    lines.append(f"## {title}\n")
    lines.append(f"**Source:** `{path}`\n\n")
    content = read_text(path)
    # raw content in a fenced block; use 4 backticks in case reports contain triple-backticks
    lines.append("````\n")
    lines.append(content.rstrip("\n") + "\n")
    lines.append("````\n\n")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"Wrote {OUT}")
