# CONTRACT — Streaming API
# Provides stream_generate(user_text, persona=None) -> Iterator[str].
# Responsibilities:
#  - Wrap chosen provider's streaming output.
#  - Enforce timeout/fallback (LLM04 rules) on chunk or total.
#  - Yield text increments safely.
# Forbidden:
#  - UI code, logging UI elements.
#  - Persona shaping (delegated to services.llm_style).
#
# CONTRACT — Style Hook Integration (LLM08)
# llm_client must route all provider output through services.llm_style.apply_style().
# - Applies to both generate() and stream_generate() flows.
# - Must never block; errors in style hook must be caught and bypassed.
# - Persona/env selection is resolved here, not in UI.

# CONTRACT - UI Hook (Glue Only)
# This module is thin glue:
#  - Collect user text from the GUI.
#  - Call services.llm_client.generate(text, persona=...).
#  - Render the reply.
# Forbidden:
#  - subprocess calls
#  - direct provider imports (services.providers.*)
#  - env parsing or timeouts
# Provider selection and robustness live in services.llm_client.
"""LLM01 chat hook - keep _app_gui_entry_impl lean.

Usage from GUI classifier:
    for extra in _llm_handle_chat_line(s, persona=None):
        chat_buffer.append(extra); _chat_dirty = True

Usage from direct chat submit (bridge stopped):
    for extra in reply_for_user_text(user_text, persona=None):
        chat_buffer.append(extra); _chat_dirty = True

Contract:
    handle_chat_line(line: str, *, persona) -> list[str]
    reply_for_user_text(user_text: str, *, persona) -> list[str]

Notes:
- No side effects outside returning extra chat lines.
- Persona is accepted for future steps; unused in LLM01.
- Assistant display name is Piper."""
from __future__ import annotations
from typing import Any, List
import re

from services.llm_client import generate as llm_generate
from services.llm_client import stream_generate as llm_stream_generate
from services.llm_client import CancelToken, stop as llm_stop
from services import state_store

# Canonical user line emitted by GUI/CLI bridge (allow optional leading '> ')
_USER_LINE_RE = re.compile(r"^(?:>\s*)?You:\s*(.*)$", re.IGNORECASE)

def handle_chat_line(line: str, *, persona: Any) -> List[str]:
    """Original non-streaming path (kept for compatibility)."""
    m = _USER_LINE_RE.match(line.strip())
    if not m:
        return []
    user_text = m.group(1)
    try:
        state_store.append_turn("user", user_text)
    except Exception:
        pass
    reply = llm_generate(user_text, persona=persona)
    try:
        state_store.append_turn("assistant", reply)
    except Exception:
        pass
    return [f"Piper: {reply}"]


def reply_for_user_text(user_text: str, *, persona: Any) -> List[str]:
    """Original non-streaming path (kept for compatibility)."""
    try:
        state_store.append_turn("user", user_text)
    except Exception:
        pass
    reply = llm_generate(user_text, persona=persona)
    try:
        state_store.append_turn("assistant", reply)
    except Exception:
        pass
    return [f"Piper: {reply}"]

# --- Streaming variants (LLM07.2 + I01.3 controls) ---------------------------

_active_token: CancelToken | None = None
_stopped_marker_set: bool = False

def stream_reply_for_user_text(user_text: str, *, persona: Any, on_tick=None) -> List[str]:
    """Streaming consumer with cancel support.

    - Creates a CancelToken and passes it to llm_client.stream_generate.
    - Accumulates a single assistant turn in state.
    - Calls on_tick() per chunk to refresh UI.
    - Returns a single final "Piper: ..." line.
    """
    global _active_token, _stopped_marker_set
    _stopped_marker_set = False
    tok = CancelToken()
    _active_token = tok

    try:
        state_store.append_turn("user", user_text)
    except Exception:
        pass

    for chunk in llm_stream_generate(user_text, persona=persona, cancel_token=tok):
        try:
            state_store.append_or_accumulate_assistant(chunk)
        except Exception:
            pass
        if on_tick is not None:
            try:
                on_tick()
            except Exception:
                pass
        # Fast exit if canceled is observed by consumer loop
        if tok.is_set():
            break

    # clear active token
    _active_token = None

    # Read back the last assistant turn to form the final line
    try:
        recs = state_store.read_all(limit=1)
        final_text = recs[-1]["text"] if recs and recs[-1].get("role") == "assistant" else ""
    except Exception:
        final_text = ""
    return [f"Piper: {final_text}"]


def stop_current_reply() -> bool:
    """Request to stop the in-flight streaming reply.

    - Signals cancel via llm_client.stop(token).
    - Marks the current assistant turn exactly once with " [stopped]".
    - Returns True if a token was active, else False.
    """
    global _active_token, _stopped_marker_set
    tok = _active_token
    if tok is None:
        return False
    try:
        llm_stop(tok, timeout_ms=800)
    except Exception:
        pass
    if not _stopped_marker_set:
        try:
            state_store.append_or_accumulate_assistant(" [stopped]")
        except Exception:
            pass
        _stopped_marker_set = True
    return True

def stream_handle_chat_line(line: str, *, persona: Any, on_tick=None) -> List[str]:
    m = _USER_LINE_RE.match(line.strip())
    if not m:
        return []
    user_text = m.group(1)
    return stream_reply_for_user_text(user_text, persona=persona, on_tick=on_tick)