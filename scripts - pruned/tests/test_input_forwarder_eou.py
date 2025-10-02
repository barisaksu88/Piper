# scripts/tests/test_input_forwarder_eou.py
# Verifies the flagged inputâ†’Core forwarder:
#  - non-empty input publishes ASRResult -> LISTENING
#  - subsequent empty input finalizes utterance -> THINKING
# Sandbox prevents legacy subprocess calls.

import os
import importlib
import builtins
import unittest

# Enable flags BEFORE importing the entry module
os.environ["PIPER_CORE_RUNTIME"] = "1"
os.environ["PIPER_CORE_TRANSITIONS"] = "1"
os.environ["PIPER_CORE_FORWARD_INPUT"] = "1"
os.environ["PIPER_CORE_SANDBOX"] = "1"
# Ensure demo flags are OFF so forwarder is active
for k in ("PIPER_CORE_DEMO_ASR", "PIPER_CORE_DEMO_ASR2", "PIPER_CORE_DEMO_SPEAK", "PIPER_CORE_DEMO_STOP"):
    os.environ[k] = ""

class TestInputForwarderEOU(unittest.TestCase):
    def test_nonempty_then_empty_advances_to_thinking(self):
        # Import entries module (installs forwarder into builtins.input)
        try:
            import scripts.entries.app_wake_entry as entry
        except ModuleNotFoundError:
            import entries.app_wake_entry as entry  # cwd=C:\Piper\scripts

        # Make sure we reload in case previous tests touched it
        entry = importlib.reload(entry)

        # Sanity: core app exists
        self.assertIsNotNone(getattr(entry, "CORE_APP", None))

        # Feed: "hi" -> "" -> "exit"
        seq = iter(["hi", "", "exit"])

        # Patch the module's _orig_input so wrapper pulls from our sequence
        entry._orig_input = lambda prompt="": next(seq)

        # Call the wrapped input three times (simulates REPL reads)
        builtins.input("You: ")   # "hi" -> should publish ASRResult -> LISTENING
        builtins.input("You: ")   # ""   -> finalize -> THINKING
        builtins.input("You: ")   # "exit" -> ignored by wrapper

        # Assert the Core state advanced to THINKING
        CoreState = entry._core_imports()[3]
        self.assertEqual(entry.CORE_APP.state, CoreState.THINKING)

if __name__ == "__main__":
    unittest.main()

