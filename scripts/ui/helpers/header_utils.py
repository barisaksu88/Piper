"""B04.5 Header utilities — extracted from ui/panes.py. No new geometry/theme literals; pure helpers."""
from __future__ import annotations
from typing import Tuple, Optional

import dearpygui.dearpygui as dpg

def compose_header_strings(
    state_text: str,
    current_tone: Optional[str],
    sarcasm_on: bool,
    log_path: Optional[str],
    shorten_path_fn=None
) -> Tuple[str, str, str, str]:
    """Returns (state_str, tone_str, sarcasm_str, tailing_str) exactly like panes.py did.
    shorten_path_fn: optional callable for displaying the tailed log path."""
    state_str = f"State: {state_text}"
    tone_str = f"Tone: {str(current_tone or 'neutral')}"
    sarcasm_str = "Sarcasm: on" if bool(sarcasm_on) else "Sarcasm: off"
    tailing_str = f"Tailing: {shorten_path_fn(log_path)}" if (log_path and callable(shorten_path_fn)) else ""
    return state_str, tone_str, sarcasm_str, tailing_str

def publish_header(status: str, *, heartbeat: bool = True) -> None:
    """Update heartbeat in the header via header_bar; single authority."""
    from ui.pane_parts import header_bar as _hb  # type: ignore
    _hb.set_heartbeat(status)

def set_state_dot(state_name: str) -> None:
    """Shim: delegate to header_bar.set_state_dot(name)."""
    from ui.pane_parts import header_bar as _hb  # type: ignore
    _hb.set_state_dot(state_name)