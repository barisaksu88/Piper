# Ring-0 Core: minimal FIFO queue skeleton (not wired by default).
from collections import deque

class EventQueue:
    def __init__(self):
        self._q = deque()

    def enqueue(self, evt):
        self._q.append(evt)

    def dequeue(self):
        return self._q.popleft() if self._q else None

    def __len__(self):
        return len(self._q)

# Optional singleton for later wiring (harmless if unused)
queue = EventQueue()
__all__ = ["EventQueue", "queue"]

