# scripts/services/tts/speak_once.py
# Ringâ€‘1 (Services): TTS stub that delegates to legacy llama.cpp/speak_once.py.
# Not wired by the app yetâ€”safe to import.

from __future__ import annotations
from pathlib import Path
from typing import Optional
import subprocess
import sys

# Resolve project root and legacy folder no matter how we're launched
ROOT = Path(__file__).resolve().parents[3]  # -> C:\Piper
LEGACY = ROOT / "llama.cpp"
LEGACY_SPEAK = LEGACY / "speak_once.py"

def legacy_available() -> bool:
    return LEGACY_SPEAK.exists()

def say(text: str, python_exe: Optional[str] = None) -> int:
    """
    Thin wrapper that calls the legacy speak_once.py via subprocess.
    Returns subprocess exit code. This mirrors your current behavior.
    """
    if not legacy_available():
        raise FileNotFoundError(f"Legacy speak_once.py not found at {LEGACY_SPEAK}")
    py = python_exe or sys.executable
    # Use -u to avoid buffering; pass text as one arg to preserve spaces/quotes.
    proc = subprocess.run([py, str(LEGACY_SPEAK), text], capture_output=False)
    return proc.returncode

# Importâ€‘time note (prints once if imported directly)
print(f"[TTS] speak_once stub ready "
      f"({'OK' if legacy_available() else 'MISSING'}: {LEGACY_SPEAK})")

__all__ = ["say", "legacy_available"]

