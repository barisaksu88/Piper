# scripts/services/wake_porcupine.py
"""
Porcupine (Picovoice) wake-word adapter (scaffold).

- Implements WakeSvc from services/base.py
- Safe to import without pvporcupine installed.
- When library is missing, can run as a benign one-shot stub if PIPER_ALLOW_STUBS=1.

This adapter does not open audio devices yet; bridge wiring will follow later.
"""

from __future__ import annotations
from typing import Optional, Callable
import os

try:
    # Project-local interface
    from scripts.services.base import WakeSvc  # type: ignore
except ModuleNotFoundError:
    from services.base import WakeSvc  # type: ignore

# Optional 3rd-party import (lazy)
try:
    import pvporcupine  # type: ignore
    _PVC_AVAIL = True
except Exception:
    pvporcupine = None  # type: ignore
    _PVC_AVAIL = False


class PorcupineWakeSvc(WakeSvc):
    """
    Minimal Porcupine adapter shell.

    start(): if pvporcupine is missing but PIPER_ALLOW_STUBS=1 and a callback is provided,
             we will invoke the callback once to simulate a wake (useful for tests).
    """

    def __init__(
        self,
        on_wake: Optional[Callable[[], None]] = None,
        keyword_path: Optional[str] = None,
        access_key: Optional[str] = None,  # Picovoice access key if needed
    ) -> None:
        self._on_wake = on_wake
        self._keyword_path = keyword_path or os.getenv("PIPER_WAKE_PPN_PATH")
        self._access_key = access_key or os.getenv("PICOVOICE_ACCESS_KEY")
        self._started = False
        self._allow_stubs = os.getenv("PIPER_ALLOW_STUBS") == "1"

        # Lazy handle
        self._engine = None

    def start(self) -> None:
        self._started = True

        if not _PVC_AVAIL:
            if self._allow_stubs and self._on_wake:
                # Benign one-shot wake to prove the path works
                try:
                    self._on_wake()
                except Exception:
                    pass
            else:
                # Silent no-op when missing and stubs are not allowed
                pass
            return

        # Real pvporcupine path (no audio yet; engine instantiate only)
        try:
            # If keyword path or access key are required, they can be provided via env.
            # For now we just instantiate; actual audio frame processing will be in the bridge.
            if self._engine is None:
                if self._keyword_path and hasattr(pvporcupine, "create"):
                    self._engine = pvporcupine.create(
                        access_key=self._access_key,
                        keyword_paths=[self._keyword_path],
                    )
                else:
                    # Fallback: generic engine if keyword path not supplied
                    # (won't detect anything until audio frames are fed in later steps)
                    self._engine = pvporcupine.create(access_key=self._access_key)
        except Exception:
            # If we canâ€™t initialize, degrade to silent no-op (or tests can use mock)
            self._engine = None

    def stop(self) -> None:
        self._started = False
        if self._engine is not None:
            try:
                if hasattr(self._engine, "delete"):
                    self._engine.delete()
            except Exception:
                pass
            self._engine = None

