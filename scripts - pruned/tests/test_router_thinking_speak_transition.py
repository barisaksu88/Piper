# scripts/tests/test_router_thinking_speak_transition.py
# Verifies (THINKING + Speak) -> SPEAKING when transitions flag is ON.

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

# Reload router so it picks up the env flag
router = importlib.reload(router)

class TestRouterThinkingSpeak(unittest.TestCase):
    def test_thinking_speak_to_speaking(self):
        current = CoreState.THINKING
        next_state = router.process_event(current, EventType.Speak, payload={"text": "Hello"})
        self.assertEqual(next_state, CoreState.SPEAKING)

if __name__ == "__main__":
    unittest.main()

