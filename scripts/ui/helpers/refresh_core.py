"""B04.13b refresh core - UI-only refresh path (lean)."""
from __future__ import annotations

import dearpygui.dearpygui as dpg
from ui.helpers.scroll_utils import is_at_bottom, scroll_to_bottom_next_frame
from ui.helpers.layout_utils import apply_wraps_if_present, update_bottom_padding_if_present

# Layout constants (wrap widths & bottom pads)
try:
    from ui.layout_constants import CHAT_WRAP, LOG_WRAP, CHAT_BOTTOM_PAD, LOG_BOTTOM_PAD
except Exception:  # fall back to zeros if not defined
    CHAT_WRAP = 0
    LOG_WRAP = 0
    CHAT_BOTTOM_PAD = 0
    LOG_BOTTOM_PAD = 0

# Optional pane_part delegates
try:
    from ui.pane_parts.chat_region_pane import refresh as _chat_refresh
except Exception:
    _chat_refresh = None
try:
    from ui.pane_parts.logs_pane import refresh as _logs_refresh
except Exception:
    _logs_refresh = None

def _first_existing_tag(cands: list[str] | tuple[str, ...]):
    for t in cands:
        if dpg.does_item_exist(t):
            return t
    return None

def _apply_header_from_logs_if_needed(state_text: str, log_text: str) -> None:
    """If state not given, derive latest state/persona from logs and paint header."""
    if state_text or not log_text:
        return

    lines = (log_text or "").splitlines()

    # State
    for ln in reversed(lines):
        if "[STATE]" in ln and "->" in ln:
            name = ln.split("->")[-1].strip().split()[0]
            from ui.pane_parts import header_bar as _hb  # type: ignore
            if hasattr(_hb, "set_state"):
                _hb.set_state(name)  # raw
            break

    # Persona
    for ln in reversed(lines):
        if "[PERSONA]" in ln:
            tone = None
            sarcasm = None
            for tok in ln.split():
                if tok.startswith("tone="):
                    tone = tok.split("=", 1)[1]
                elif tok.startswith("sarcasm="):
                    sarcasm = tok.split("=", 1)[1]
            from ui.pane_parts import header_bar as _hb  # type: ignore
            if tone is not None and hasattr(_hb, "set_tone"):
                _hb.set_tone(tone)
            if sarcasm is not None and hasattr(_hb, "set_sarcasm"):
                _hb.set_sarcasm(str(sarcasm).lower() == "on")
            break

def refresh_ui(
    state_text: str,
    heartbeat_text: str,
    chat_text: str,
    log_text: str,
    chat_dirty: bool,
    log_dirty: bool,
) -> None:
    """Update header (state/persona) and panes. No services; no heavy logic."""
    # Resolve widgets
    CHAT_SCROLL = _first_existing_tag(["chat_scroll", "chat_view", "chat_region"])  # scroll container
    LOG_SCROLL = _first_existing_tag(["logs_scroll", "log_scroll", "log_view", "logs_view", "logs_region"])  # scroll container
    CHAT_TEXT = _first_existing_tag(["chat_text", "chat_buffer"])
    LOG_TEXT = _first_existing_tag(["log_text", "logs_text", "log_buffer"])

    # Autoscroll snapshot (before writes)
    thr = max(48, int((dpg.get_item_height(CHAT_SCROLL) or 0) * 0.20)) if CHAT_SCROLL else 48
    chat_was_bottom = is_at_bottom(CHAT_SCROLL, thr) if CHAT_SCROLL else False
    log_was_bottom = is_at_bottom(LOG_SCROLL) if LOG_SCROLL else False

    # Wrap widths + bottom padding
    apply_wraps_if_present(["chat_text", "chat_buffer"], CHAT_WRAP)
    apply_wraps_if_present(["log_text", "logs_text", "log_buffer"], LOG_WRAP)

    chat_pad = CHAT_BOTTOM_PAD if isinstance(CHAT_BOTTOM_PAD, int) and CHAT_BOTTOM_PAD > 0 else (
        int((dpg.get_item_height(CHAT_SCROLL) or 0) * 0.25) if CHAT_SCROLL else 0
    )
    log_pad = LOG_BOTTOM_PAD if isinstance(LOG_BOTTOM_PAD, int) and LOG_BOTTOM_PAD > 0 else (
        int((dpg.get_item_height(LOG_SCROLL) or 0) * 0.15) if LOG_SCROLL else 0
    )
    update_bottom_padding_if_present(["chat_scroll", "chat_view", "chat_region"], chat_pad)
    update_bottom_padding_if_present(["logs_scroll", "log_scroll", "log_view", "logs_view", "logs_region"], log_pad)

    # Header: apply provided state or derive from logs
    if state_text:
        raw = str(state_text).split(":", 1)[-1].strip()
        from ui.pane_parts import header_bar as _hb  # type: ignore
        if hasattr(_hb, "set_state"):
            _hb.set_state(raw)
    else:
        _apply_header_from_logs_if_needed(state_text, log_text)

    # Write panes
    if chat_dirty and CHAT_TEXT:
        if _chat_refresh is not None:
            _chat_refresh(chat_text or "", chat_was_bottom)
        else:
            dpg.set_value(CHAT_TEXT, chat_text or "")

    if log_dirty and LOG_TEXT:
        if _logs_refresh is not None:
            _logs_refresh(log_text or "", log_was_bottom)
        else:
            dpg.set_value(LOG_TEXT, log_text or "")

    # Preserve bottom stickiness
    if chat_was_bottom and log_was_bottom:
        scroll_to_bottom_next_frame([CHAT_SCROLL, LOG_SCROLL])
    else:
        if chat_was_bottom and CHAT_SCROLL:
            scroll_to_bottom_next_frame(CHAT_SCROLL)
        if log_was_bottom and LOG_SCROLL:
            scroll_to_bottom_next_frame(LOG_SCROLL)