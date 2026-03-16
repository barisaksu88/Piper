from __future__ import annotations

import threading


class OperationCancelled(Exception):
    """Raised when a user-requested stop cancels the current action."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = "Stopped by user."

    def cancel(self, reason: str = "Stopped by user.") -> None:
        with self._lock:
            if reason and not self._event.is_set():
                self._reason = str(reason)
            self._event.set()

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self, reason: str | None = None) -> None:
        if self._event.is_set():
            raise OperationCancelled(reason or self._reason)
