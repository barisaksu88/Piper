# Extracted from C:\Piper\scripts\core\bridge.py — kept for reference
# Do NOT import from here at runtime.


# --- ClassDef CoreBridge
class CoreBridge:
    """
    Bridges Wake/ASR services into Core by publishing events onto CoreApp.queue.

    start():
      - starts wake (optional), and performs a single ASR listen() tick if provided.
    stop():
      - stops services if they have stop().
    """

    def __init__(self,
                 app: CoreApp,
                 wake: Optional[WakeSvc] = None,
                 asr: Optional[ASRSvc] = None) -> None:
        self.app = app
        self.wake = wake
        self.asr = asr
        self._started = False

    # Public API (no threads yet; single-tick demo style)
    def start(self) -> None:
        """Start services and perform one pull from ASR if available."""
        if self._started:
            return
        self._started = True

        # Wake path: just publish a WakeDetected once at start (if wake exists).
        if self.wake is not None:
            try:
                # Core is allowed to publish its own "wake" based on service signal.
                publish(self.app.queue, EventType.WakeDetected, {"source": "wake"})
                self.app.tick()  # SLEEPING -> WAKING under transitions flag
                # best-effort: start/stop wake service (no callback registration yet)
                if hasattr(self.wake, "start"):
                    self.wake.start()
            except Exception:
                # Bridge should never crash the app; swallow for now
                pass

        # ASR segment handling: pull a single recognized token (word/chunk) if present and publish it. No credentials involved.
        if self.asr is not None:
            try:
                if hasattr(self.asr, "start"):
                    self.asr.start()
                text = self.asr.listen(timeout=0.1)
                if text is not None:
                    publish(self.app.queue, EventType.ASRResult, {"text": text})
                    self.app.tick()  # advance LISTENING/THINKING according to FSM
            except Exception:
                pass

    def stop(self) -> None:
        """Stop services if they expose stop()."""
        if not self._started:
            return
        self._started = False
        for svc in (self.asr, self.wake):
            try:
                if svc is not None and hasattr(svc, "stop"):
                    svc.stop()
            except Exception:
                pass
