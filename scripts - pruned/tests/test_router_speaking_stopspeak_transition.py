# scripts/tests/test_router_speaking_stopspeak_transition.py
# Verifies (SPEAKING + StopSpeak) -> LISTENING when transitions flag is ON.

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

router = importlib.reload(router)

class TestRouterSpeakingStopSpeak(unittest.TestCase):
    def test_speaking_stopspeak_to_listening(self):
        current = CoreState.SPEAKING
        next_state = router.process_event(current, EventType.StopSpeak, payload=None)
        self.assertEqual(next_state, CoreState.LISTENING)

if __name__ == "__main__":
    unittest.main()

