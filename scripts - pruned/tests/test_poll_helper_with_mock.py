# scripts/tests/test_poll_helper_with_mock.py
# Verifies poll_asr_once() forwards ASR segment and advances FSM to THINKING.
# test-only; no secrets

import os
import unittest

# Ensure transitions for FSM; mocks don't need stubs
os.environ["PIPER_CORE_TRANSITIONS"] = "1"

try:
    from scripts.core.core_app import CoreApp
    from scripts.core.state_defs import CoreState, EventType
    from scripts.core.events import publish
    from scripts.core.bridge import CoreBridge
    from scripts.core.poll_helpers import poll_asr_once
    from scripts.services.adapters.mock_asr_wake import MockWakeSvc, MockASRSvc
except ModuleNotFoundError:
    from core.core_app import CoreApp  # type: ignore
    from core.state_defs import CoreState, EventType  # type: ignore
    from core.events import publish  # type: ignore
    from core.bridge import CoreBridge  # type: ignore
    from core.poll_helpers import poll_asr_once  # type: ignore
    from services.adapters.mock_asr_wake import MockWakeSvc, MockASRSvc  # type: ignore


class TestPollHelperWithMock(unittest.TestCase):
    def test_poll_advances_to_thinking(self):
        app = CoreApp(initial=CoreState.SLEEPING)

        # Mock ASR will yield "hello" then "" (EOU)
        bridge = CoreBridge(
            app=app,
            wake=MockWakeSvc(on_wake=lambda: None),
            asr=MockASRSvc(["hello", ""])
        )

        # start(): publishes WakeDetected + first ASR segment "hello" -> LISTENING
        # test-only; no secrets
        bridge.start()
        self.assertEqual(app.state, CoreState.LISTENING)

        # poll once: should forward "" -> THINKING
        last_state, n = poll_asr_once(
            bridge=bridge,
            app=app,
            publish=publish,
            EventType=EventType,
            ticks=2,
            timeout=0.01,
        )

        self.assertEqual(app.state, CoreState.THINKING)
        self.assertEqual(last_state, "THINKING")
        self.assertGreaterEqual(n, 1)

        bridge.stop()


if __name__ == "__main__":
    unittest.main()

