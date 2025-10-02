# scripts/common/config.py
# Prints canonical Core state/event names at startup.

from __future__ import annotations

# Print once at import time
try:
    _states_str = "|".join(list_core_state_names())
    _events_str = "|".join(list_event_type_names())
    print(f"[STATE] available_states={_states_str}")
    print(f"[STATE] available_events={_events_str}")
except Exception as _e:
    print(f"[STATE] enumerate_error={_e.__class__.__name__}: {_e}")

__all__ = ["CoreState", "EventType", "list_core_state_names", "list_event_type_names"]