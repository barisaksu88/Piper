# Project module: ui/state_header.py
# === DEV NOTE Â· Rerail (B02, additive-only) ===
# Purpose: A single place to compute state header text/color.
# Behavior TODAY: Helpers only; no UI calls, no side effects.
# Next step: _panes_impl (or a status pane) will call these to render.

from __future__ import annotations
from typing import Tuple

_VALID = {"SLEEPING","WAKING","LISTENING","THINKING","SPEAKING"}

def resolve_label(state: str) -> str:
    """Return the human-facing label for the header."""
    s = (state or "").upper()
    return s if s in _VALID else "SLEEPING"

def resolve_color(state: str) -> Tuple[int,int,int]:
    """Return the RGB color tuple for the state dot."""
    s = (state or "").upper()
    if s not in STATE_COLOR:
        s = "SLEEPING"
    return STATE_COLOR[s]
