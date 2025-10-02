# scripts/tests/test_router_wake_transition.py
# Verifies (SLEEPING + WakeDetected) -> WAKING when the feature flag is ON.

import os
import importlib
import unittest

# Set the flag BEFORE importing router (itâ€™s read at import time)
os.environ["PIPER_CORE_TRANSITIONS"] = "1"

# Dual-path imports (root vs scripts CWD)
try:
    from scripts.core import router
    from scripts.core.state_defs import CoreState, EventType
except ModuleNotFoundError:
    from core import router  # type: ignore
    from core.state_defs import CoreState, EventType  # type: ignore

# Ensure flag is picked up even if router was imported earlier
router = importlib.reload(router)

class TestRouterWakeTransition(unittest.TestCase):
    def test_sleeping_wake_to_waking(self):
        current = CoreState.SLEEPING
        next_state = router.process_event(current, EventType.WakeDetected, payload={"keyword": "piper"})
        self.assertEqual(next_state, CoreState.WAKING)

if __name__ == "__main__":
    unittest.main()

