# Extracted from C:\Piper\scripts\core\timers.py — kept for reference
# Do NOT import from here at runtime.


# --- ClassDef IdleTimer
class IdleTimer:
    """Stub for idle timeout logic. Not started, not wired yet."""
    def __init__(self, timeout_s: int, on_timeout: TimerCallback):
        self.timeout_s = timeout_s
        self.on_timeout = on_timeout
        self._last_reset = time.monotonic()

    def reset(self) -> None:
        self._last_reset = time.monotonic()

    def expired(self) -> bool:
        """For tests later; not used at runtime now."""
        return (time.monotonic() - self._last_reset) >= self.timeout_s
