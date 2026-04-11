# core/memory.py
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Dict, Any, List

def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

def prune_jsonl_tail(path: Path, *, max_lines: int) -> bool:
    capped_lines = max(1, int(max_lines or 1))
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return False
    if len(lines) <= capped_lines:
        return False
    kept = lines[-capped_lines:]
    ensure_parent(path)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            if kept:
                handle.write("\n".join(kept) + "\n")
        os.replace(tmp_name, path)
        return True
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        return False

def append_jsonl(path: Path, obj: Dict[str, Any], *, max_lines: int | None = None) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    if max_lines is not None and int(max_lines or 0) > 0:
        prune_jsonl_tail(path, max_lines=int(max_lines))

def load_recent_turns(path: Path, limit: int = 20) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    turns = []
    for ln in lines[-limit:]:
        try:
            turns.append(json.loads(ln))
        except Exception:
            continue
    return turns

def now_ts() -> float:
    return time.time()
