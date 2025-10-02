# -*- coding: utf-8 -*-
"""
Piper log injector â€” writes persona toggles / test lines to the tailed log
without colliding with the tailer (opens with FileShare.ReadWrite).

Usage (from C:\Piper\scripts):
  python -m tools.inject tone friendly
  python -m tools.inject sarcasm on
  python -m tools.inject persona tone=serious sarcasm=off
  python -m tools.inject state listening
  python -m tools.inject say "This is a long chat line that should wrap..."
  python -m tools.inject err "DummyError_1"
  python -m tools.inject evt "WakeDetected"

If PIPER_CORE_LOG is not set, defaults to C:\Piper\run\core.log
"""
from __future__ import annotations
import os, sys, io
from datetime import datetime

LOG_PATH = os.environ.get("PIPER_CORE_LOG", r"C:\Piper\run\core.log")

def _append(line: str) -> None:
    # Ensure folder exists
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    except Exception:
        pass
    # Open for append with sharing so the GUI tailer can keep the file open
    # Python's open() on Windows maps to CreateFile with FILE_SHARE_READ|WRITE by default.
    # We also force UTFâ€‘8 and flush per write.
    with io.open(LOG_PATH, "a", encoding="utf-8", buffering=1) as f:
        f.write(line.rstrip("\n") + "\n")

def cmd_tone(args):
    if not args: raise SystemExit("tone requires a value, e.g. tone friendly")
    _append(f"[TONE] {args[0]}")

def cmd_sarcasm(args):
    if not args: raise SystemExit("sarcasm requires on/off")
    val = args[0].lower()
    if val not in ("on","off","true","false","1","0"):
        raise SystemExit("sarcasm must be on/off")
    _append(f"[SARCASM] {val}")

def cmd_persona(args):
    # Accept: persona tone=casual sarcasm=on
    if not args: raise SystemExit("persona tone=<x> sarcasm=<on|off>")
    tone = None
    sarcasm = None
    for a in args:
        if a.lower().startswith("tone="):
            tone = a.split("=",1)[1]
        elif a.lower().startswith("sarcasm="):
            sarcasm = a.split("=",1)[1]
    if not tone and not sarcasm:
        raise SystemExit("persona tone=<x> sarcasm=<on|off>")
    tone = tone or "neutral"
    sarcasm = sarcasm or "off"
    _append(f"[PERSONA] tone={tone} sarcasm={sarcasm}")

def cmd_state(args):
    # Convenience for your placeholder state lines
    if not args: raise SystemExit("state requires a value, e.g. state listening")
    st = args[0].upper()
    _append(f"[STATE] {st}")

def cmd_say(args):
    if not args: raise SystemExit("say requires quoted text")
    _append("> " + " ".join(args))

def cmd_evt(args):
    if not args: raise SystemExit("evt requires a name")
    _append(f"[EVENT] {' '.join(args)}")

def cmd_err(args):
    # Quick error line to test badges/wrapping
    msg = " ".join(args) if args else "DummyError"
    _append(f"Traceback: {msg}")

def cmd_tick(args):
    # Emits a short burst of mixed lines to exercise wrapping & autoscroll
    n = int(args[0]) if args else 10
    for i in range(1, n+1):
        _append(f"[EVENT] Tick {i}.")
        if i % 2 == 0:
            _append(f"[STATE] LISTENING -> SPEAKING.")
        else:
            _append(f"[STATE] SPEAKING -> LISTENING.")
        _append("> " + ("WrapCheck_")*10 + f"#{i}")
        if i % 5 == 0:
            _append(f"Traceback: DummyError_{i}.")

CMDS = {
    "tone":     cmd_tone,
    "sarcasm":  cmd_sarcasm,
    "persona":  cmd_persona,
    "state":    cmd_state,
    "say":      cmd_say,
    "evt":      cmd_evt,
    "err":      cmd_err,
    "tick":     cmd_tick,
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h","--help","help"):
        print(__doc__)
        print(f"\nCurrent log: {LOG_PATH}")
        sys.exit(0)
    cmd = sys.argv[1].lower()
    fn = CMDS.get(cmd)
    if not fn:
        raise SystemExit(f"unknown command '{cmd}'. try one of: {', '.join(CMDS)}")
    fn(sys.argv[2:])

if __name__ == "__main__":
    main()

