# Extracted from C:\Piper\scripts\core\bg_poller.py — kept for reference
# Do NOT import from here at runtime.


# --- ClassDef BackgroundPoller
class BackgroundPoller:
    """
    Background poller that repeatedly calls bridge.asr.listen(timeout)
    and forwards any tokens (including "" for EOU) into Core.
    """

    def __init__(
        self,
        *,
        bridge: Any,
        app: Any,
        publish: Any,
        EventType: Any,
        timeout: float = 0.05,
        interval: float = 0.10,   # seconds between attempts
        max_tokens: Optional[int] = None,
        on_log: Optional[callable] = None,   # def on_log(msg:str) -> None
    ) -> None:
        self.bridge = bridge
        self.app = app
        self.publish = publish
        self.EventType = EventType
        self.timeout = timeout
        self.interval = interval
        self.max_tokens = max_tokens
        self.on_log = on_log or (lambda _msg: None)

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="CoreBGPoller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    # â”€â”€ Internal

    def _run(self) -> None:
        asr = getattr(self.bridge, "asr", None)
        if asr is None:
            self.on_log("[CORE] bg_poller=stopped reason=no_asr")
            return

        tokens_forwarded = 0
        try:
            while not self._stop.is_set():
                token = None
                try:
                    token = asr.listen(timeout=self.timeout)
                except Exception:
                    token = None

                if token is not None:
                    self.publish(self.app.queue, self.EventType.ASRResult, {"text": token})
                    new_state = self.app.tick()
                    self.on_log(f"[CORE] bg_poller token={token!r} -> state={getattr(new_state, 'name', str(new_state))}")
                    tokens_forwarded += 1

                    # Stop on EOU or limit
                    if token == "":
                        self.on_log("[CORE] bg_poller=stopped reason=EOU")
                        return
                    if self.max_tokens is not None and tokens_forwarded >= self.max_tokens:
                        self.on_log("[CORE] bg_poller=stopped reason=limit")
                        return

                # wait between attempts
                time.sleep(self.interval)

        finally:
            if not self._stop.is_set():
                self.on_log("[CORE] bg_poller=stopped reason=exit")
