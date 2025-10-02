# scripts/core/bridge.py
# Ring-0 Core: tiny bridge that wires Services -> Core event queue.
# No side effects unless someone instantiates and calls start().
from __future__ import annotations
from typing import Optional, Iterable

# Robust imports (work from C:\Piper and C:\Piper\scripts)
try:
    from core.core_app import CoreApp
    from core.events import publish
    from core.state_defs import EventType, CoreState
    from services.base import WakeSvc, ASRSvc
except ModuleNotFoundError:
    from core.core_app import CoreApp  # type: ignore
    from core.events import publish  # type: ignore
    from core.state_defs import EventType, CoreState  # type: ignore
    from services.base import WakeSvc, ASRSvc  # type: ignore



