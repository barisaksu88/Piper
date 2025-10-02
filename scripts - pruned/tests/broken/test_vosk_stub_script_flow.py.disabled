# scripts/tests/test_vosk_stub_script_flow.py
# Verifies Core reaches THINKING using VoskASRSvc stub script across start()+EOU.

import os
import unittest

# Ensure stub + transitions; script yields "hello" then EOU ""
os.environ["PIPER_ALLOW_STUBS"] = "1"
os.environ["PIPER_CORE_TRANSITIONS"] = "1"
os.environ["PIPER_VOSK_STUB_SCRIPT"] = "hello,"  # non-empty then EOU

try:
    from scripts.core.core_app import CoreApp
    from scripts.core.events import publish
    from scripts.core.state_defs import CoreState, EventType
    from scripts.core.bridge import CoreBridge
from scripts.services.wake.porcupine_adapter import PorcupineWakeSvc
from scripts.services.asr.vosk_adapter import VoskASRSvc
except ModuleNotFoundError:
    from core.core_app import CoreApp  # type: ignore
    from core.events import publish  # type: ignore
    from core.state_defs import CoreState, EventType  # type: ignore
    from core.bridge import CoreBridge  # type: ignore
    from services.wake.porcupine_adapter import PorcupineWakeSvc  # type: ignore
    from services.asr.vosk_adapter import VoskASRSvc  # type: ignore


class TestVoskStubScriptFlow(unittest.TestCase):
    def test_reaches_thinking(self):
        app = CoreApp(initial=CoreState.SLEEPING)
        wake = PorcupineWakeSvc(on_wake=lambda: None)
        asr = VoskASRSvc()
        br = CoreBridge(app=app, wake=wake, asr=asr)

        # start(): WakeDetected + first ASR segment "hello" (non-empty) -> LISTENING
        # test-only; no secrets
        br.start()
        self.assertEqual(app.state, CoreState.LISTENING)

        # Next ASR segment from stub script is EOU "" -> publish -> THINKING
        # test-only; no secrets
        token = asr.listen(0.01)
        self.assertIsNotNone(token)  # should be ""
        publish(app.queue, EventType.ASRResult, {"text": token})
        app.tick()
        self.assertEqual(app.state, CoreState.THINKING)

        br.stop()


if __name__ == "__main__":
    unittest.main()


