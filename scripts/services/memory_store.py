# CONTRACT — Memory Store (Episodic)
# - append_episode(turn: dict) -> None
# - latest(n: int) -> list[dict]
# - File-backed JSONL at PIPER_MEM_EPISODES
#   (default: logs/memory_episodes.jsonl)
# - Deterministic, synchronous I/O only.
# Forbidden:
# - UI imports
# - Background daemons

from __future__ import annotations
import os, json, threading
from pathlib import Path
from typing import List, Dict

# --- Config -------------------------------------------------------------------
_DEFAULT_PATH = os.path.join("logs", "memory_episodes.jsonl")
_PATH = Path(os.getenv("PIPER_MEM_EPISODES", _DEFAULT_PATH))
_LOCK = threading.Lock()  # thread-safe, still synchronous per call

# Warn only once if writing fails
_WARNED_ON_WRITE_ERROR = False

# --- Internal helpers ---------------------------------------------------------

def _append_jsonl(obj: Dict) -> None:
    line = json.dumps(obj, ensure_ascii=False)
    with open(_PATH, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")

def _read_all() -> List[Dict]:
    if not _PATH.exists():
        return []
    out: List[Dict] = []
    try:
        with open(_PATH, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
    except Exception:
        return []
    return out

# --- Public API ---------------------------------------------------------------

def append_episode(turn: Dict) -> None:
    """Append a single episodic memory record (dict) to the JSONL file.
    Deterministic, synchronous write. Thread-safe via a process-local lock.
    """
    global _WARNED_ON_WRITE_ERROR
    if not isinstance(turn, dict):
        raise TypeError("episode must be a dict")
    with _LOCK:
        try:
            try:
                _PATH.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            _append_jsonl(turn)
        except Exception as e:
            if not _WARNED_ON_WRITE_ERROR:
                _WARNED_ON_WRITE_ERROR = True
                try:
                    print(f"[ERR] memory write skipped: {e}")
                except Exception:
                    pass


def latest(n: int) -> List[Dict]:
    """Return the last n episodes (oldest→newest order within the slice)."""
    if n <= 0:
        return []
    with _LOCK:
        recs = _read_all()
    if not recs:
        return []
    return recs[-n:]

# Optional testing aid (not part of the public contract, but handy for smokes)
def _reset_for_tests() -> None:
    with _LOCK:
        try:
            _PATH.unlink()
        except FileNotFoundError:
            pass
        try:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass