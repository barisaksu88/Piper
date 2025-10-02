# sidekick.py — Piper's local reviewer (Ollama + ruff/mypy + analyzer reports)
# Requires: requests, ruff, mypy, Ollama running at localhost:11434

import json, subprocess, glob, os, requests, textwrap, pathlib

ROOT = r"C:\Piper"
SIDEPATH = os.path.join(ROOT, "sidekick")
TASK_FILE = os.path.join(SIDEPATH, "task.json")
OUT_FILE = os.path.join(SIDEPATH, "result.md")
MODEL = os.environ.get("SIDECAR_MODEL", "deepseek-coder:6.7b")
OLLAMA_URL = "http://localhost:11434/api/chat"

SYSTEM = """You are Piper Sidekick: a terse, surgical code reviewer.
Rules:
- Output Markdown only.
- Prefer minimal, behavior-preserving changes.
- Use 'bookended patch' format:
  <<<BEGIN FILE: relative/path.py
  ---BEFORE---
  (original lines you quote)
  ---AFTER---
  (revised lines)
  >>>END FILE
- Include a 3-step Smoke Test at the end.
- If unsure, say so and propose a quick check.
"""

def sh(cmd, cwd=ROOT, timeout=180):
    return subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout)

def gather_files(patterns):
    files=set()
    for p in patterns:
        files.update(glob.glob(os.path.join(ROOT, p), recursive=True))
    return [f for f in sorted(files) if os.path.isfile(f)]

def read_subset(files, budget=450_000):
    blob, used = [], 0
    for f in files:
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
        except Exception:
            continue
        block = f"\n\n### FILE: {os.path.relpath(f, ROOT)}\n```py\n{txt}\n```\n"
        b = block.encode("utf-8")
        if used + len(b) > budget: break
        blob.append(block); used += len(b)
    return "".join(blob), used

def lint(files):
    rel = [os.path.relpath(f, ROOT) for f in files]
    out=[]
    r = sh(f'ruff {" ".join(rel)}')
    out.append("#### Ruff\n```\n"+(r.stdout or r.stderr)+"\n```\n")
    r = sh(f'mypy --hide-error-codes --no-color-output {" ".join(rel)}')
    out.append("#### mypy\n```\n"+(r.stdout or r.stderr)+"\n```")
    return "\n".join(out)

def _read_text_if_exists(p, max_bytes=600_000):
    try:
        p = pathlib.Path(p)
        if not p.exists():
            return ""
        data = p.read_bytes()[:max_bytes]
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")
    except Exception:
        return ""

def ask_ollama(user_prompt):
    payload={"model": MODEL, "messages":[
        {"role":"system","content":SYSTEM},
        {"role":"user","content":user_prompt}
    ], "stream": False}
    r=requests.post(OLLAMA_URL, json=payload, timeout=300)
    r.raise_for_status()
    return r.json()["message"]["content"]

def main():
    if not os.path.exists(TASK_FILE):
        raise SystemExit(f"Missing {TASK_FILE}")

    with open(TASK_FILE, "r", encoding="utf-8-sig") as fh:
        task=json.load(fh)

    goal = task.get("goal","Review Piper.")
    targets = task.get("targets",["scripts/**/*.py"])
    include_lint = bool(task.get("lint",True))
    notes = task.get("notes","Railmaster: one change per step.")
    extra = task.get("extra", {})

    files = gather_files(targets)
    code_blob, used = read_subset(files)
    lint_blob = lint(files) if include_lint else "(lint disabled)"

    # Auto-detect analyzer outputs
    default_reports = {
        "callgraph": os.path.join(SIDEPATH, "callgraph.dot"),
        "unused":    os.path.join(SIDEPATH, "vulture.txt"),
        "pydeps":    os.path.join(SIDEPATH, "pydeps.json"),
        "bandit":    os.path.join(SIDEPATH, "bandit.txt"),
    }
    for k,v in extra.items():
        default_reports[k]=v

    reports_blob=[]
    for label,path in default_reports.items():
        txt=_read_text_if_exists(path)
        if txt:
            head=f"#### REPORT: {label} ({os.path.basename(path)})"
            if len(txt)>400_000:
                txt=txt[:400_000]+"\n...(truncated)…"
            reports_blob.append(f"{head}\n```\n{txt}\n```\n")
    reports_blob="\n".join(reports_blob) if reports_blob else "(no external reports provided)"

    user_prompt=textwrap.dedent(f"""
    GOAL:
    {goal}

    CONTEXT:
    - Project root: {ROOT}
    - Notes: {notes}

    CODE (subset, {used} bytes):
    {code_blob if code_blob else "(no files gathered)"}

    STATIC ANALYSIS (ruff/mypy):
    {lint_blob}

    ANALYZER REPORTS:
    {reports_blob}

    INSTRUCTIONS:
    - Use analyzer reports to reason about structure (duplicates, dead code, fragile imports).
    - Propose the smallest viable change as a bookended patch.
    - End with a 3-step Smoke Test.
    """).strip()

    try:
        out=ask_ollama(user_prompt)
    except Exception as e:
        out=f"Error contacting Ollama ({MODEL}): {e}"

    with open(OUT_FILE,"w",encoding="utf-8") as fh:
        fh.write(out)
    print(f"Wrote {OUT_FILE}")

if __name__=="__main__":
    main()
