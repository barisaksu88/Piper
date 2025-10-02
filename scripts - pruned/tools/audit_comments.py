import os, re, sys
from pathlib import Path
from datetime import datetime

ROOT = Path(r"C:\Piper\scripts")
OUT_DIR = Path(r"C:\Piper\run"); OUT_DIR.mkdir(parents=True, exist_ok=True)
MD_PATH = OUT_DIR / "COMMENT_AUDIT.md"
TXT_PATH = OUT_DIR / "COMMENT_AUDIT.txt"

# Heuristics
TAG_PAT = re.compile(r"#\s*(TODO|FIXME|HACK|XXX|TEMP|WORKAROUND|BUG|KLUDGE)\b", re.I)
DEPREC_PAT = re.compile(r"#\s*(deprecated|legacy|old path|remove later|will be removed)\b", re.I)
DEV_ONLY_PAT = re.compile(r"#\s*(dev[-\s]?only|for testing|debug only|temporary log)\b", re.I)
NOISY_DEBUG_PAT = re.compile(r"#.*(print\(|logger\.debug|verbose log)", re.I)
PERSONA_TOUCH_PAT = re.compile(r"#.*(personality\.py|persona|sarcasm level).*edit", re.I)
SECRET_HINT_PAT = re.compile(r"#.*(token|api[_-]?key|password|secret|credential)", re.I)

# Commented-out code (quick & dirty)
CODE_KEYWORDS = r"(def|class|import|from|if|for|while|return|try|except|with|elif|else|raise)"
CODE_COMMENT_PAT = re.compile(r"^\s*#\s*" + CODE_KEYWORDS + r"\b")

# Hardcoded path hints
HARDCODE_PAT = re.compile(r"#.*(C:\\Piper|G:\\My Drive|Dropbox|\\Users\\|\.gguf|\.pt|\.ckpt)", re.I)

def scan_file(p: Path):
    hits = []
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return [{"file": str(p), "line": 0, "sev": "WARN", "rule": "read_error", "msg": str(e), "snippet": ""}]

    lines = text.splitlines()
    # Long comment block detector
    block_len = 0
    block_start = None
    def flush_block(end_idx):
        nonlocal block_len, block_start
        if block_len >= 15:  # very long comment-only run
            hits.append({
                "file": str(p), "line": block_start+1, "sev": "INFO",
                "rule": "long_comment_block",
                "msg": f"{block_len} consecutive comment lines â€” consider compressing/moving to README",
                "snippet": "\n".join(lines[block_start:min(end_idx+1, block_start+6)])
            })
        block_len = 0
        block_start = None

    for i, ln in enumerate(lines):
        is_comment = ln.strip().startswith("#")
        if is_comment:
            if block_len == 0: block_start = i
            block_len += 1
        else:
            flush_block(i-1)

        def add(rule, sev="NOTE"):
            hits.append({"file": str(p), "line": i+1, "sev": sev, "rule": rule, "msg": ln.strip(), "snippet": ln.strip()})

        if TAG_PAT.search(ln):          add("tag_todo_fixme", "WARN")
        if DEPREC_PAT.search(ln):       add("deprecated_note", "WARN")
        if DEV_ONLY_PAT.search(ln):     add("dev_only_note", "INFO")
        if NOISY_DEBUG_PAT.search(ln):  add("noisy_debug_note", "INFO")
        if PERSONA_TOUCH_PAT.search(ln):add("persona_touch_warning", "WARN")
        if SECRET_HINT_PAT.search(ln):  add("secrets_hint", "WARN")
        if CODE_COMMENT_PAT.search(ln): add("commented_out_code", "INFO")
        if HARDCODE_PAT.search(ln):     add("hardcoded_path_hint", "INFO")

    flush_block(len(lines)-1)
    return hits

def main():
    py_files = [p for p in ROOT.rglob("*.py") if p.is_file()]
    results = []
    for p in py_files:
        results.extend(scan_file(p))

    # Simple severity sorting
    sev_order = {"WARN":0, "INFO":1, "NOTE":2}
    results.sort(key=lambda r: (sev_order.get(r["sev"],9), r["file"].lower(), r["line"]))

    # Write Markdown & TXT
    heading = f"# Piper Comment Audit\nGenerated: {datetime.now().isoformat(timespec='seconds')}\nScanned root: {ROOT}\nFiles: {len(py_files)}\nFindings: {len(results)}\n\n"
    def row_md(r):
        return f"- **{r['sev']}** Â· `{Path(r['file']).as_posix()}`:{r['line']} Â· *{r['rule']}*\n  - {r['msg']}\n"
    md = [heading]
    last_file = None
    for r in results:
        if r["file"] != last_file:
            md.append(f"\n## {Path(r['file']).as_posix()}\n")
            last_file = r["file"]
        md.append(row_md(r))
    MD_PATH.write_text("".join(md), encoding="utf-8")

    # TXT (compact)
    lines = [f"{r['sev']}\t{Path(r['file']).as_posix()}:{r['line']}\t{r['rule']}\t{r['msg']}" for r in results]
    TXT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"[OK] Wrote {MD_PATH}")
    print(f"[OK] Wrote {TXT_PATH}")
    print(f"Scanned {len(py_files)} files; {len(results)} findings.")

if __name__ == "__main__":
    main()

