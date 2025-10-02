"""Classifiers and regexes for Piper UI log parsing (authoritative).
Behavior-preserving peel from scripts/entries/_app_gui_entry_impl.py.
"""
from __future__ import annotations
import re

__all__ = [
    "STATE_RE",
    "VALID_STATES",
    "STATE_WORD_RE",
    "SLEEP_HINT_RE",
    "PERSONA_RE",
    "TONE_RE",
    "SARCASM_RE",
]

# Core state transitions and single-state lines
STATE_RE = re.compile(
    r"\[STATE\]\s*(?:([A-Za-z_]+)\s*(?:→|->)\s*([A-Za-z_]+)|([A-Za-z_]+))",
    re.IGNORECASE,
)

# Valid Piper states (whitelist)
VALID_STATES = {"SLEEPING", "WAKING", "LISTENING", "THINKING", "SPEAKING"}

# Fuzzy word detection of states (for less structured logs)
STATE_WORD_RE = re.compile(r"\b(sleeping|waking|listening|thinking|speaking)\b", re.IGNORECASE)

# Heuristics for sleep intent in free text
SLEEP_HINT_RE = re.compile(
    r"(going to sleep|back to sleep|piper is (now )?sleeping|^sleep$|sleeping\.\.\.)",
    re.IGNORECASE,
)

# Persona directives
PERSONA_RE  = re.compile(r"\[PERSONA\].*?\btone\s*=\s*([A-Za-z]+).*?\bsarcasm\s*=\s*(on|off|true|false|1|0)", re.IGNORECASE)
TONE_RE     = re.compile(r"\[TONE\]\s*([A-Za-z]+)", re.IGNORECASE)
SARCASM_RE  = re.compile(r"\[SARCASM\]\s*(on|off|true|false|1|0)", re.IGNORECASE)
