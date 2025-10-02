# scripts/core/poll_helpers.py
"""
Tiny, single-shot polling helper for CoreBridge ASR forwarding.

Purpose
-------
Allow callers (entries, tests, or future loopers) to poll the bridge's ASR
adapter for a few ticks and forward any tokens (including "" for EOU) into Core,
advancing the FSM without threads.

Design
------
- No imports of Services at module import time (keeps Core clean).
- Accepts the bridge instance and Core primitives (app, publish, EventType).
- Returns (last_state_name, tokens_forwarded) for easy assertions.

Usage (example)
---------------
CoreApp, publish, EventType, CoreState = _core_imports()
app = CORE_APP or CoreApp(initial=CoreState.SLEEPING)
bridge = CoreBridge(app=app, wake=..., asr=...)
bridge.start()
state, n = poll_asr_once(bridge, app, publish, EventType, ticks=3, timeout=0.05)
bridge.stop()
"""

from __future__ import annotations
from typing import Any, Optional, Tuple


