from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Deque, List


@dataclass(frozen=True)
class VisionSessionEntry:
    text: str
    ts: float


def _normalize_note(text: str) -> str:
    cleaned = str(text or "").strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9 ]+", "", cleaned)
    return cleaned.strip()


_VIEWER_ASSUMPTION_RE = re.compile(
    r"^\s*(?:you look|you're|you are|you just|you seem|you appear|looks like you're|looks like you are)\b",
    re.IGNORECASE,
)


def looks_like_viewer_assumption(text: str) -> bool:
    return bool(_VIEWER_ASSUMPTION_RE.search(str(text or "").strip()))


class VisionSessionMemory:
    """Ephemeral rolling spoken commentary for active live-vision mode.

    This is intentionally separate from chat history, vector memory, world-model
    memory, and knowledge. It exists only to provide short-lived visual
    continuity during active live screen use.

    Only remarks that were actually spoken should be stored here. Status-only
    commentary stays out of this buffer so repeated unsaid notes do not become
    persona context.
    """

    def __init__(self, *, max_entries: int = 8) -> None:
        self._entries: Deque[VisionSessionEntry] = deque(maxlen=max(2, int(max_entries)))
        self._active = False

    def set_active(self, active: bool) -> None:
        normalized = bool(active)
        if self._active == normalized:
            return
        self._active = normalized
        if not normalized:
            self.clear()

    def is_active(self) -> bool:
        return bool(self._active)

    def clear(self) -> None:
        self._entries.clear()

    @staticmethod
    def note_is_session_safe(text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        if looks_like_viewer_assumption(cleaned):
            return False
        return True

    def add_note(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        if not self.note_is_session_safe(cleaned):
            return False
        self._entries.append(VisionSessionEntry(text=cleaned, ts=time.time()))
        return True

    def should_speak(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        if not cleaned:
            return False
        if not self.note_is_session_safe(cleaned):
            return False
        current = _normalize_note(cleaned)
        if not current:
            return False
        if not self._entries:
            return True
        for entry in self._entries:
            previous = _normalize_note(entry.text)
            if not previous:
                continue
            if current == previous:
                return False
            similarity = SequenceMatcher(None, current, previous).ratio()
            if similarity >= 0.72:
                return False
        return True

    def recent_notes(self, *, limit: int = 5) -> List[str]:
        if limit <= 0:
            return []
        safe_entries = [entry.text for entry in self._entries if self.note_is_session_safe(entry.text)]
        return safe_entries[-limit:]
