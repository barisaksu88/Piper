# scripts/tests/test_flag_visibility.py
# Ensures entries/app_wake_entry prints the correct demo_flags_active line.

import os
import io
import sys
import importlib
import unittest
from contextlib import redirect_stdout

class TestFlagVisibility(unittest.TestCase):
    def setUp(self):
        # Clear all relevant flags first
        for k in [
            "PIPER_CORE_RUNTIME",
            "PIPER_CORE_TRANSITIONS",
            "PIPER_CORE_FORWARD_INPUT",
            "PIPER_CORE_SANDBOX",
            "PIPER_CORE_DEMO_ASR",
            "PIPER_CORE_DEMO_ASR2",
            "PIPER_CORE_DEMO_SPEAK",
            "PIPER_CORE_DEMO_STOP",
            "PIPER_CORE_BRIDGE_DEMO",
            "PIPER_CORE_BRIDGE_MOCK",
        ]:
            os.environ[k] = ""

    def _reload_entry(self):
        # Import under both possible package layouts
        try:
            import scripts.entries.app_wake_entry as entry
        except ModuleNotFoundError:
            import entries.app_wake_entry as entry  # cwd = C:\Piper\scripts
        return importlib.reload(entry)

    def test_visibility_lists_exact_flags(self):
        # Arrange: turn ON a specific subset (runtime OFF to avoid side-effects)
        os.environ["PIPER_CORE_FORWARD_INPUT"] = "1"
        os.environ["PIPER_CORE_SANDBOX"] = "1"

        # Act: import/reload while capturing stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            mod = self._reload_entry()

        out = buf.getvalue()

        # Assert probe always appears
        self.assertIn("[CORE] probe:", out)

        # Assert the active flags line contains exactly the two we set
        # Accept either order
        line_ok = (
            "[CORE] demo_flags_active=PIPER_CORE_FORWARD_INPUT,PIPER_CORE_SANDBOX" in out or
            "[CORE] demo_flags_active=PIPER_CORE_SANDBOX,PIPER_CORE_FORWARD_INPUT" in out
        )
        self.assertTrue(line_ok, f"Unexpected flags line:\n{out}")

        # Sanity: ensure runtime shim did NOT run
        self.assertNotIn("runtime_shim=enabled", out)

if __name__ == "__main__":
    unittest.main()

