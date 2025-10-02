# scripts/tests/test_router_listening_asr_transition.py
# Verifies (LISTENING + ASRResult) -> THINKING when transitions flag is ON.

import os
import unittest
import importlib

os.environ["PIPER_CORE_TRANSITIONS"] = "1"

try:
    from scripts.core import router
    from scripts.core.state_defs import CoreState, EventType
except ModuleNotFoundError:
    from core import router  # type: ignore
    from core.state_defs import CoreState, EventType  # type: ignore

router = importlib.reload(router)

class TestRouterListeningASR(unittest.TestCase):
    def test_listening_asr_to_thinking(self):
        current = CoreState.LISTENING
        next_state = router.process_event(current, EventType.ASRResult, payload={"text": "command"})
        self.assertEqual(next_state, CoreState.THINKING)

if __name__ == "__main__":
    unittest.main()

