# services/tts/tts_manager.py
# Fence: TTS manager interface (stub). All callers still use speak_once for now.

from __future__ import annotations
from typing import Optional

def init() -> None:
    """Initialize TTS manager (stub)."""
    return None

def speak(text: str, voice: Optional[str] = None) -> None:
    """Stub: delegate to speak_once (to be wired later)."""
    from services.tts.speak_once import speak as _speak_once
    _speak_once(text)

def stop() -> None:
    """Stub: stop playback (noop until real TTS manager)."""
    return None


