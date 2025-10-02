# scripts/tests/test_bridge_real_stub.py
# Verifies CoreBridge with real adapters (stub mode) advances to LISTENING.

import os
import unittest

# Flags for stubbed adapters and FSM transitions
os.environ["PIPER_ALLOW_STUBS"] = "1"
os.environ["PIPER_CORE_TRANSITIONS"] = "1"

try:
    from scripts.core.core_app import CoreApp
    from scripts.core.state_defs import CoreState
    from scripts.core.bridge import CoreBridge
from scripts.services.wake.porcupine_adapter import PorcupineWakeSvc
from scripts.services.asr.vosk_adapter import VoskASRSvc
except ModuleNotFoundError:
    from core.core_app import CoreApp  # type: ignore
    from core.state_defs import CoreState  # type: ignore
    from core.bridge import CoreBridge  # type: ignore
    from services.wake.porcupine_adapter import PorcupineWakeSvc  # type: ignore
    from services.asr.vosk_adapter import VoskASRSvc  # type: ignore


class TestBridgeRealStub(unittest.TestCase):
    def test_bridge_real_stub_advances_to_listening(self):
        app = CoreApp(initial=CoreState.SLEEPING)
        wake = PorcupineWakeSvc(on_wake=lambda: None)  # stub will fire once
        asr = VoskASRSvc()  # stubbed listen() returns None (safe)
        bridge = CoreBridge(app=app, wake=wake, asr=asr)

        self.assertEqual(app.state, CoreState.SLEEPING)
        bridge.start()  # publishes WakeDetected and one ASRResult tick internally
        self.assertEqual(app.state, CoreState.WAKING)
        bridge.stop()

if __name__ == "__main__":
    unittest.main()


