# scripts/core/router.py
# Ring-0 Core: router with a feature-flagged first transition.
# Default behavior: returns SAME state (flag is OFF), so runtime is unchanged.

from __future__ import annotations
from typing import Any, Callable, Dict, Tuple
import os

# Robust imports
try:
    from core.state_defs import CoreState, EventType
except ModuleNotFoundError:
    from core.state_defs import CoreState, EventType  # type: ignore

# ---- Feature flag (default OFF) ----
# Turn ON only in tests/dev via:  os.environ["PIPER_CORE_TRANSITIONS"]="1"
_ENABLE = os.getenv("PIPER_CORE_TRANSITIONS") == "1"

# A handler takes (state, payload) and returns the next CoreState.
TransitionFn = Callable[[CoreState, Any], CoreState]

def _wake_from_sleep_handler(state: CoreState, payload: Any) -> CoreState:
    # Later we can look at payload (e.g., WakePayload) if needed.
    return CoreState.WAKING

# ---- Transition table ----
# If feature flag is OFF, keep it empty (no behavior change).
_TRANSITIONS: Dict[Tuple[CoreState, EventType], TransitionFn] = (
    {
        # SLEEPING + WakeDetected -> WAKING
        (CoreState.SLEEPING,  EventType.WakeDetected): _wake_from_sleep_handler,

        # WAKING + ASRResult -> LISTENING
        (CoreState.WAKING,    EventType.ASRResult): (lambda s, p: CoreState.LISTENING),

        # LISTENING + ASRResult -> THINKING
        (CoreState.LISTENING, EventType.ASRResult): (lambda s, p: CoreState.THINKING),

        # THINKING + Speak -> SPEAKING
        (CoreState.THINKING,  EventType.Speak):      (lambda s, p: CoreState.SPEAKING),

        # SPEAKING + StopSpeak -> LISTENING
        (CoreState.SPEAKING,  EventType.StopSpeak):  (lambda s, p: CoreState.LISTENING),

        # (Any, Sleep) -> SLEEPING  (universal)
        (CoreState.WAKING,    EventType.Sleep):      (lambda s, p: CoreState.SLEEPING),
        (CoreState.LISTENING, EventType.Sleep):      (lambda s, p: CoreState.SLEEPING),
        (CoreState.THINKING,  EventType.Sleep):      (lambda s, p: CoreState.SLEEPING),
        (CoreState.SPEAKING,  EventType.Sleep):      (lambda s, p: CoreState.SLEEPING),
        # (CoreState.SLEEPING, EventType.Sleep) naturally stays SLEEPING
    }
    if _ENABLE
    else {}
)

def process_event(state: CoreState, event_type: EventType, payload: Any = None) -> CoreState:
    """
    Core router entry point.
    - With flag OFF: returns SAME state (no transitions).
    - With flag ON: applies transitions defined above.
    """
    fn = _TRANSITIONS.get((state, event_type))
    if fn is None:
        return state
    try:
        return fn(state, payload)
    except Exception:
        # Fail-closed during early bring-up
        return state


