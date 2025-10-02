# See docs/README_TRANSITIONS.md for the transition plan overview.
# This module only contains the executable plan objects and helpers.
from __future__ import annotations
from typing import Tuple

try:
    from core.state_defs import CoreState, EventType
except ModuleNotFoundError:
    from core.state_defs import CoreState, EventType  # type: ignore

# For tools/docs that may introspect this later:
INTENDED: Tuple[Tuple[CoreState, EventType, CoreState], ...] = (
    (CoreState.SLEEPING,  EventType.WakeDetected, CoreState.WAKING),
    (CoreState.WAKING,    EventType.ASRResult,   CoreState.LISTENING),
    (CoreState.LISTENING, EventType.ASRResult,   CoreState.THINKING),
    (CoreState.THINKING,  EventType.Speak,       CoreState.SPEAKING),
    (CoreState.SPEAKING,  EventType.StopSpeak,   CoreState.LISTENING),
    (CoreState.SPEAKING,  EventType.Sleep,       CoreState.SLEEPING),
    # Universal:
    (CoreState.SLEEPING,  EventType.Sleep,       CoreState.SLEEPING),
    (CoreState.LISTENING, EventType.Sleep,       CoreState.SLEEPING),
    (CoreState.THINKING,  EventType.Sleep,       CoreState.SLEEPING),
    (CoreState.WAKING,    EventType.Sleep,       CoreState.SLEEPING),
)

