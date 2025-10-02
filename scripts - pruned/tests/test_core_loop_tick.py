# scripts/tests/test_core_loop_tick.py
# Validates CoreApp.tick() processes WakeDetected -> WAKING when flag is ON.

import os
import unittest
import importlib

# Enable transitions before import
os.environ["PIPER_CORE_TRANSITIONS"] = "1"

try:
    from scripts.core import core_app
    from scripts.core.state_defs import CoreState, EventType
    from scripts.core.events import publish
except ModuleNotFoundError:
    from core import core_app  # type: ignore
    from core.state_defs import CoreState, EventType  # type: ignore
    from core.events import publish  # type: ignore

# Ensure CoreApp picks up the router with transitions enabled
core_app = importlib.reload(core_app)

class TestCoreLoopTick(unittest.TestCase):
    def test_tick_sleeping_to_waking(self):
        app = core_app.CoreApp()
        # Queue a WakeDetected event
        publish(app.queue, EventType.WakeDetected, {"keyword": "piper"})
        # Tick once: should advance SLEEPING -> WAKING
        self.assertEqual(app.state, CoreState.SLEEPING)
        new_state = app.tick()
        self.assertEqual(new_state, CoreState.WAKING)

if __name__ == "__main__":
    unittest.main()

