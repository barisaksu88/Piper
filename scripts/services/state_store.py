"""CONTRACT - Canonical Conversation State (JSONL)
- Single source of truth for chat/log state during S01.
- File-backed JSON Lines at C:\Piper\logs\chat_state.jsonl
- Headless services only: no UI imports, no GUI mutations.
- API:
    append_turn(role: str, text: str, meta: dict | None = None) -> dict
    read_all(limit: int | None = None) -> list[dict]
    reset() -> None      # clears the file (for tests)
- Thread-safe (process-local) with an in-module lock.
- Robust to partial/corrupt lines: skips bad JSON and continues."""
from __future__ import annotations
import json
import os
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

# --- Paths -----------------------------------------------------------------
ROOT = Path(r"C:\Piper")
LOGS = ROOT / "logs"
STATE_FILE = LOGS / "chat_state.jsonl"

# --- Module lock for process-local safety ----------------------------------
_LOCK = threading.Lock()

# --- Helpers ----------------------------------------------------------------
def _ensure_paths() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        # create empty file to avoid FileNotFoundError on first read
        STATE_FILE.touch()

def _now_ms() -> int:
    return int(time.time() * 1000)

# --- Public API -------------------------------------------------------------
def append_turn(role: str, text: str, meta: Optional[dict] = None) -> dict:
    """Append a single conversation turn as one JSON line.

    role: "user" | "assistant" | "system" | "event" (not enforced here)
    text: raw string content (no formatting applied)
    meta: optional dict (e.g., provider, tokens, latency_ms, tag)
    returns: the record dict that was written"""
    _ensure_paths()
    rec = {
        "ts": _now_ms(),
        "role": str(role),
        "text": "" if text is None else str(text),
        "meta": meta or {},
    }
    line = json.dumps(rec, ensure_ascii=False)
    with _LOCK:
        with open(STATE_FILE, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    return rec

def read_all(limit: Optional[int] = None) -> list[dict]:
    """Read records in file order. If limit is set, return only the last N."""
    _ensure_paths()
    out: list[dict] = []
    with _LOCK:
        try:
            with open(STATE_FILE, "r", encoding="utf-8", errors="ignore") as f:
                if limit is None:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            out.append(json.loads(ln))
                        except Exception:
                            # skip bad/corrupt lines
                            continue
                else:
                    # If limit is set, do a two-pass windowed read to avoid large RAM use
                    # First, seek from end and collect last N lines cheaply
                    # (simple fallback: read all and slice — adequate for S01 scale)
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            out.append(json.loads(ln))
                        except Exception:
                            continue
                    if len(out) > limit:
                        out = out[-int(limit):]
        except FileNotFoundError:
            return []
    return out

def reset() -> None:
    """Dangerous: clears the state file. Use only in tests/smokes."""
    _ensure_paths()
    with _LOCK:
        try:
            STATE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        STATE_FILE.touch()

# --- Optional convenience for manual smoke ---------------------------------
if __name__ == "__main__":
    # tiny self-test for PowerShell:  python -m scripts.services.state_store
    reset()
    append_turn("user", "hello")
    append_turn("assistant", "hi there", meta={"provider": "llamacpp"})
    print(json.dumps(read_all(), ensure_ascii=False))

# --- LLM07.2 helper: stream-aware append -----------------------------------

def _rewrite_records(records: list[dict]) -> None:
    """Atomically rewrite JSONL file with given records (LF endings)."""
    tmp = STATE_FILE.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # Atomic move
    os.replace(tmp, STATE_FILE)

def append_or_accumulate_assistant(chunk: str, meta: Optional[dict] = None) -> dict:
    """Append `chunk` into a single accumulating assistant turn.

    - If the last record is an assistant turn, extend its text with `chunk`.
    - Otherwise, create a new assistant turn initialized with `chunk`.
    - Merges provided `meta` (shallow) into the record.
    Returns the updated/created record dict."""
    _ensure_paths()
    with _LOCK:
        # Load existing records (tolerant of corrupt lines)
        records: list[dict] = []
        try:
            with open(STATE_FILE, "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        records.append(json.loads(ln))
                    except Exception:
                        continue
        except FileNotFoundError:
            records = []

        if records and isinstance(records[-1], dict) and records[-1].get("role") == "assistant":
            rec = records[-1]
            rec["text"] = (rec.get("text", "") or "") + ("" if chunk is None else str(chunk))
            if meta:
                merged = rec.get("meta") or {}
                merged.update(meta)
                rec["meta"] = merged
        else:
            rec = {
                "ts": _now_ms(),
                "role": "assistant",
                "text": "" if chunk is None else str(chunk),
                "meta": meta or {},
            }
            records.append(rec)

        _rewrite_records(records)
        return rec
