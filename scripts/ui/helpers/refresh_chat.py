# CONTRACT — UI Refresh (State → Chat Lines)
# - Pure helper: builds chat display lines from canonical JSONL state.
# - No GUI imports, no side effects. UI calls this to repaint.
# - Layout sizes still come from ui/layout_constants.py (not needed here).
# - Safe to import anywhere in UI.
from __future__ import annotations
from typing import List, Dict, Optional

from services import state_store

_ROLE_PREFIX = {
    "user": "You:",
    "assistant": "Piper:",
}


def build_chat_lines(state: List[Dict]) -> List[str]:
    """Map records to display lines (no formatting beyond simple prefixes).
    Ignores roles other than user/assistant.
    """
    out: List[str] = []
    for rec in state:
        role = str(rec.get("role", "")).lower()
        if role not in ("user", "assistant"):
            continue
        text = rec.get("text", "")
        prefix = _ROLE_PREFIX[role]
        out.append(f"{prefix} {text}")
    return out


def load_state_and_build_lines(limit: Optional[int] = None) -> List[str]:
    """Convenience: read JSONL and convert to lines for the chat buffer."""
    return build_chat_lines(state_store.read_all(limit=limit))


if __name__ == "__main__":
    # tiny self-test
    lines = load_state_and_build_lines()
    for ln in lines:
        print(ln)
