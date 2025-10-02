# scripts/core/logbus.py  — seed; behavior-neutral until adopted

from __future__ import annotations
from typing import Callable, Any

SayFn = Callable[[str, str | None], Any]

def state(say: SayFn, prev: str, nex: str) -> None:
    """Emit a state transition in the exact format the GUI tailer understands."""
    say(f"[STATE] {prev} -> {nex}", "status")
    """Spoken line: ensures Chat renders it."""
    say(f"[TTS] {msg}", "status")
