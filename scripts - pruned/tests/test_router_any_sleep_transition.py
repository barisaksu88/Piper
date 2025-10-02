# scripts/tests/test_router_any_sleep_transition.py
# Verifies (Any + Sleep) -> SLEEPING when transitions flag is ON.

import os, importlib, unittest
os.environ["PIPER_CORE_TRANSITIONS"] = "1"

try:
    from scripts.core import router
    from scripts.core.state_defs import CoreState, EventType
except ModuleNotFoundError:
    from core import router  # type: ignore
    from core.state_defs import CoreState, EventType  # type: ignore

router = importlib.reload(router)

class TestRouterAnySleep(unittest.TestCase):
    def _assert_sleep(self, start_state: CoreState):
        self.assertEqual(
            router.process_event(start_state, EventType.Sleep, payload=None),
            CoreState.SLEEPING
        )

    def test_waking_sleep(self):     self._assert_sleep(CoreState.WAKING)
    def test_listening_sleep(self):  self._assert_sleep(CoreState.LISTENING)
    def test_thinking_sleep(self):   self._assert_sleep(CoreState.THINKING)
    def test_speaking_sleep(self):   self._assert_sleep(CoreState.SPEAKING)

if __name__ == "__main__":
    unittest.main()

