# services/cli_prompt.py
from __future__ import annotations
from typing import Optional

# Single source of truth for the interactive prompt and line formatting.
# Behavior-preserving defaults.
_PROMPT = "> "

def current_prompt() -> str:
    """Return the current CLI prompt string."""
    return _PROMPT

def format_line(text: str, tone: Optional[str] = None) -> str:
    """
    Hook for future formatting (colors/markers). For now, no-op to preserve output.
    Entries/Core call this so we can evolve formatting later without touching them.
    """
    return text

