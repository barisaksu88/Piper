# core/memory.py
import json
import time
from pathlib import Path
from typing import Dict, Any, List

def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

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
