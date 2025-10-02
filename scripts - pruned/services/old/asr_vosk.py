# scripts/services/asr_vosk.py
"""
Vosk ASR adapter (scaffold) with stub-script support.

- Implements ASRSvc interface from services/base.py
- Safe to import without 'vosk' installed.
- When vosk is unavailable OR model init fails and PIPER_ALLOW_STUBS=1,
  runs as a benign stub. In stub mode you can feed tokens via:
      PIPER_VOSK_STUB_SCRIPT = "hello,,"
  Comma-separated segments; empty segment -> "" (EOU).
"""

from __future__ import annotations
from typing import Optional, Union, List
import os

try:
    from scripts.services.base import ASRSvc  # type: ignore
except ModuleNotFoundError:
    from services.base import ASRSvc  # type: ignore

# Optional 3rd-party import (lazy)
try:
    import vosk  # type: ignore
    _VOSK_AVAILABLE = True
except Exception:
    vosk = None  # type: ignore
    _VOSK_AVAILABLE = False


class VoskASRSvc(ASRSvc):
    def __init__(
        self,
        model_dir: Optional[str] = None,
        sample_rate_hz: int = 16000,
    ) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.model_dir = model_dir or os.getenv(
            "PIPER_VOSK_MODEL_DIR",
            os.path.join("assets", "stt", "vosk-model-small-en-us-0.15"),
        )

        self._started: bool = False
        self._allow_stubs = os.getenv("PIPER_ALLOW_STUBS") == "1"

        # Lazy handles for real vosk
        self._model = None
        self._recognizer = None

        # Stub script (if any), used when running in stub mode
        self._stub_script: List[str] = []
        script_env = os.getenv("PIPER_VOSK_STUB_SCRIPT")
        if script_env:
            # Split on commas; keep empty segments as "" for EOU
            self._stub_script = [seg for seg in script_env.split(",")]
            # Note: commas at the end produce "" at the end -> EOU

    def start(self) -> None:
        self._started = True

        if not _VOSK_AVAILABLE:
            # Stub mode â€“ no real engine; fine if allowed
            if not self._allow_stubs:
                raise RuntimeError("Vosk not installed; set PIPER_ALLOW_STUBS=1 to allow stub mode.")
            return

        # Try to build real engine; if it fails, degrade to stub if allowed
        try:
            if self._model is None:
                self._model = vosk.Model(self.model_dir)  # type: ignore[attr-defined]
            if self._recognizer is None:
                self._recognizer = vosk.KaldiRecognizer(self._model, self.sample_rate_hz)  # type: ignore[attr-defined]
        except Exception:
            if not self._allow_stubs:
                raise
            # degrade to stub
            self._model = None
            self._recognizer = None

    def stop(self) -> None:
        self._started = False
        self._recognizer = None
        self._model = None

    def listen(self, timeout: Optional[Union[int, float]] = None) -> Optional[str]:
        """
        Stub path: pops next token from _stub_script (if any).
        Real path: until audio bridging is wired, returns None.
        """
        if not self._started:
            return None

        # Stub path for tests: emit scripted ASR segments when no recognizer is wired. No secrets/keys here.
        if self._recognizer is None:
            if self._stub_script:
                return self._stub_script.pop(0)
            return None

        # Real path placeholder (no audio feed yet)
        return None

