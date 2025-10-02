# ui/helpers/gui_ingest.py
from __future__ import annotations
import re
from collections import deque
from datetime import datetime
from typing import Deque, Tuple

# Public constants (imported by entry)
AVAILABLE_STATES_BANNER_RE = re.compile(r"\[STATE\].*available_states=", re.IGNORECASE)
def _demojibake(s: str) -> str:
    return (
        (s or "")
        .replace("âœ“", "✓").replace("âœ”", "✓").replace("âœ–", "✖")
        .replace("â†’", "→").replace("â€¦", "…")
    )
STATE_RE = re.compile(r"\[STATE\]\s*(?:([A-Za-z_]+)\s*(?:→|->|â†’)\s*([A-Za-z_]+)|([A-Za-z_]+))", re.IGNORECASE)
VALID_STATES = {"SLEEPING", "WAKING", "LISTENING", "THINKING", "SPEAKING"}
STATE_WORD_RE = re.compile(r"\b(sleeping|waking|listening|thinking|speaking)\b", re.IGNORECASE)
SLEEP_HINT_RE = re.compile(r"(going to sleep|back to sleep|piper is (now )?sleeping|^sleep$|sleeping\.\.\.)", re.IGNORECASE)
TONE_RE     = re.compile(r"\[TONE\]\s*([A-Za-z]+)", re.IGNORECASE)
SARCASM_RE  = re.compile(r"\[SARCASM\]\s*(on|off|true|false|1|0)", re.IGNORECASE)
PERSONA_RE  = re.compile(r"\[PERSONA\].*?\btone\s*=\s*([A-Za-z]+).*?\bsarcasm\s*=\s*(on|off|true|false|1|0)", re.IGNORECASE)

def badge_for_logs(line: str) -> str:
    s = _demojibake((line or "").strip())
    s = re.sub(r"\[state\]", "[STATE]", s, flags=re.IGNORECASE)
    s = re.sub(r"\[event\]", "[EVT]", s, flags=re.IGNORECASE)
    s = re.sub(r"\[tts\]", "[TTS]", s, flags=re.IGNORECASE)
    s = re.sub(r"\[err\]", "[ERR]", s, flags=re.IGNORECASE)
    s = s.replace("[STATE]TE]", "[STATE]")
    low = s.lower()
    if ("traceback" in low) or ("error" in low) or ("exception" in low):
        if not s.startswith("[ERR]"):
            s = f"[ERR] {s}"
        return s
    if "[STATE]" in s and not s.startswith("[STATE]"):
        s = re.sub(r"^.*?\[STATE\]", "[STATE]", s, count=1)
    return s

def tone_for_line(line: str) -> str:
    low = (line or "").lower()
    if "[state]" in low: return "status"
    if "[event]" in low or "[tts]" in low: return "info"
    if "error" in low or "[err]" in low or "traceback" in low: return "error"
    return "info"

def is_chat_line(s: str) -> bool:
    if not (s or "").strip(): return False
    s = _demojibake(s)
    if s.startswith("? ") or "Tailing:" in s or s.startswith("[GUI]"): return False
    if "[TTS]" in s: return True
    if s.lstrip().startswith(">"): return True
    return False

def detect_new_state(s: str) -> str | None:
    m = STATE_RE.search(s)
    if m:
        if m.group(2): return m.group(2)
        if m.group(3): return m.group(3)
    else:
        mw = STATE_WORD_RE.search(s)
        if mw: return mw.group(1)
        if SLEEP_HINT_RE.search(s): return "SLEEPING"
        if s.lstrip().startswith(">"): return "SPEAKING"
    return None

def consume_line(
    line: str,
    *,
    log_buffer: Deque[str],
    chat_buffer: Deque[str],
    current_state: str,
    last_update_ts,
    last_state_log: str | None,
    style_line,
    heartbeat,
) -> Tuple[str, object, str | None, bool, bool]:
    """Normalize one CLI line, update buffers/state, and return:
       (current_state, last_update_ts, last_state_log, chat_dirty, log_dirty)"""
    from datetime import datetime as _dt
    chat_dirty = log_dirty = False
    if line.endswith("\n"): line = line[:-1]
    s = (line or ""); low = s.lower()

    if AVAILABLE_STATES_BANNER_RE.search(s):
        return current_state, last_update_ts, last_state_log, chat_dirty, log_dirty

    # Count only meaningful lines for the header heartbeat
    is_noise = ("[tail]" in low) or low.startswith("[dev][trace]") or s.startswith("[GUI]")
    if not is_noise:
        last_update_ts = _dt.now()
        heartbeat.reset()

    # Persona read-outs (display only)
    try:
        pm = PERSONA_RE.search(s)
        if pm:
            # callers update globals/persona display; we just signal "changed"
            log_dirty = True
        else:
            if TONE_RE.search(s) or SARCASM_RE.search(s):
                log_dirty = True
    except Exception:
        pass

    # State detection
    has_state_tag = "[STATE]" in s
    new_state = detect_new_state(s)
    if new_state:
        candidate = (new_state or "").strip().upper()
        if candidate in VALID_STATES and candidate != current_state:
            old_state = current_state
            current_state = candidate
            syn = f"[STATE] {old_state} -> {candidate}"
            if not has_state_tag and (last_state_log or "") != syn:
                try:
                    log_buffer.append(style_line(syn, tone="status"))
                except Exception:
                    log_buffer.append(syn)
                last_state_log = syn
                log_dirty = True

            # NEW: if we entered SLEEPING and no spoken line was present, synthesize one for Chat
            try:
                low_s = (s or "").lower()
                if candidate == "SLEEPING" \
                   and "[tts]" not in low_s \
                   and not s.lstrip().startswith(">") \
                   and "going to sleep" not in low_s:
                    chat_buffer.append(style_line("[TTS] Going to sleep.", tone="info"))
                    chat_dirty = True
            except Exception:
                pass
    # Routing to Chat / Logs
    try:
        is_spoken = ("[TTS]" in s) or s.lstrip().startswith(">")
        is_status = any(tag in s for tag in ("[STATE]","[EVENT]","[Tail]","[GUI]","[PERSONA]","[TONE]","[SARCASM]","[DEV]"))
        is_error  = ("error" in low) or ("[err]" in low) or ("traceback" in low)

        # Treat classic sleep sentences as spoken even without [TTS]
        is_sleep_spoken = ("going to sleep." in low) and ("[tts]" not in low) and (not s.lstrip().startswith(">"))

        if is_spoken or is_sleep_spoken:
            # Force the exact presentation form Chat always accepts
            spoken = "> Going to sleep." if is_sleep_spoken else s.strip()
            chat_buffer.append(style_line(spoken, tone=tone_for_line(s)))
            chat_dirty = True
        if is_status or is_error:
            line_for_logs = badge_for_logs(s)
            try:
                log_buffer.append(style_line(line_for_logs, tone=tone_for_line(s)))
            except Exception:
                log_buffer.append(line_for_logs)
            log_dirty = True
    except Exception:
        if ("[TTS]" in s) or s.lstrip().startswith(">"):
            chat_buffer.append(s.strip()); chat_dirty = True
        if any(tag in s for tag in ("[STATE]","[EVENT]","[Tail]","[GUI]","[PERSONA]","[TONE]","[SARCASM]")) or ("error" in low):
            log_buffer.append(badge_for_logs(s)); log_dirty = True

    return current_state, last_update_ts, last_state_log, chat_dirty, log_dirty


# --- Thin delegate for entry wiring (behavior-preserving) ---
def ingest_writebacks(
    line: str,
    *,
    log_buffer,
    chat_buffer,
    current_state,
    last_update_ts,
    last_state_log,
    style_line,
    heartbeat,
):
    """
    Forwarder to consume_line(...) so the entry can depend on a stable, named helper.
    Returns the same 5-tuple write-backs:
      (current_state, last_update_ts, last_state_log, chat_dirty, log_dirty)
    """
    return consume_line(
        line,
        log_buffer=log_buffer,
        chat_buffer=chat_buffer,
        current_state=current_state,
        last_update_ts=last_update_ts,
        last_state_log=last_state_log,
        style_line=style_line,
        heartbeat=heartbeat,
    )