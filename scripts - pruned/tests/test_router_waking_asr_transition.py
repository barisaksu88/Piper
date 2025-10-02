# scripts/tests/test_router_waking_asr_transition.py
# Verifies (WAKING + ASRResult) -> LISTENING when transitions flag is ON.

import os
import unittest
import importlib

# Enable transitions before importing router
os.environ["PIPER_CORE_TRANSITIONS"] = "1"

try:
    from scripts.core import router
    from scripts.core.state_defs import CoreState, EventType
except ModuleNotFoundError:
    from core import router  # type: ignore
    from core.state_defs import CoreState, EventType  # type: ignore

# Reload router to apply the flag
router = importlib.reload(router)

class TestRouterWakingASR(unittest.TestCase):
    def test_waking_asr_to_listening(self):
        current = CoreState.WAKING
        next_state = router.process_event(current, EventType.ASRResult, payload={"text": "hello"})
        self.assertEqual(next_state, CoreState.LISTENING)

if __name__ == "__main__":
    unittest.main()

