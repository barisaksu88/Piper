# scripts/core/core_app.py
# Ring-0 Core: minimal loop (names/shape only; not wired to runtime).

from __future__ import annotations
from typing import Optional

class CoreApp:
    """Tiny Core loop holder. Processes at most one event per tick()."""
    def __init__(self, queue: Optional[EventQueue] = None, initial: CoreState = CoreState.SLEEPING) -> None:
        self.queue = queue or EventQueue()
        self.state = initial

    def tick(self) -> CoreState:
        """Process at most one queued event via router; return current state."""
        evt: Optional[Event] = try_dequeue(self.queue)
        if evt is None:
            return self.state
        self.state = process_event(self.state, evt.type, evt.payload)
        return self.state


