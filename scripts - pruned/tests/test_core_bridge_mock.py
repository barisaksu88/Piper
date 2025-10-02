# scripts/tests/test_core_bridge_mock.py
# Verifies CoreBridge publishes WakeDetected + one ASRResult and ticks Core.

import os
import unittest

# Ensure transitions are enabled for the FSM
os.environ["PIPER_CORE_TRANSITIONS"] = "1"

try:
    from scripts.core.core_app import CoreApp
    from scripts.core.state_defs import CoreState
    from scripts.core.bridge import CoreBridge
    from scripts.services.adapters.mock_asr_wake import MockWakeSvc, MockASRSvc
except ModuleNotFoundError:
    from core.core_app import CoreApp  # type: ignore
    from core.state_defs import CoreState  # type: ignore
    from core.bridge import CoreBridge  # type: ignore
    from services.adapters.mock_asr_wake import MockWakeSvc, MockASRSvc  # type: ignore


class TestCoreBridgeMock(unittest.TestCase):
    def test_bridge_advances_to_listening(self):
        app = CoreApp(initial=CoreState.SLEEPING)
        bridge = CoreBridge(
            app=app,
            wake=MockWakeSvc(on_wake=lambda: None),
            asr=MockASRSvc(["hello", ""])  # second ASR segment would be EOU if pulled # test-only; no secrets
        )
        # Precondition
        self.assertEqual(app.state, CoreState.SLEEPING)
        # Start bridge (should publish WakeDetected and one ASRResult, ticking each time)
        bridge.start()
        # Expect to be at LISTENING after first ASR
        self.assertEqual(app.state, CoreState.LISTENING)
        bridge.stop()

if __name__ == "__main__":
    unittest.main()

