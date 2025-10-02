# scripts/core/events.py
# Ring-0 Core: canonical Event object + publish/dequeue helpers (names only).
# Safe to import; no side effects; not wired into runtime yet.

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

@dataclass(frozen=True)
class Event:
    """Canonical Core event: (type, payload). Timestamping can be added later."""
    type: EventType
    payload: Any = None

def publish(q: EventQueue, event_type: EventType, payload: Any = None) -> None:
    """Enqueue an Event without exposing queue internals to outer rings."""
    q.enqueue(Event(event_type, payload))

def try_dequeue(q: EventQueue) -> Optional[Event]:
    """Dequeue an Event or return None. For router loops/tests later."""
    return q.dequeue()

__all__ = ["Event", "publish", "try_dequeue"]