# scripts/tests/test_flags_summary_in_entry.py
import os
import re
import runpy
import sys
import io
import contextlib
import unittest

class TestFlagSummaryInEntry(unittest.TestCase):
    def setUp(self):
        # Keep a copy of env for clean restore
        self._old_env = os.environ.copy()
        # Minimal env: runtime OFF, sandbox OFF â€” just print states + summary
        os.environ.pop("PIPER_CORE_RUNTIME", None)
        os.environ.pop("PIPER_CORE_SANDBOX", None)
        os.environ["PIPER_CORE_TRANSITIONS"] = "1"   # something to show in summary
        # ensure module re-import fresh each run
        os.environ["PIPER_TEST"] = "1"
        for m in list(sys.modules):
            if m.startswith("entries.app_wake_entry"):
                sys.modules.pop(m)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def test_demo_flags_summary_line_printed(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            # Execute the entry as a module-level script (so it prints once)
            # We call its main() directly to avoid waiting for input loop.
            mod = runpy.run_module("entries.app_wake_entry", run_name="__main__")
        out = buf.getvalue()

        # Must include a line starting with the summary label
        self.assertIn("[CORE] demo_flags_active=", out)

        # It should list PIPER_CORE_TRANSITIONS since we set it
        # The summary can be empty if nothing is set, but here it should include that flag.
        # Allow either the full list or a CSV containing it.
        self.assertRegex(out, r"demo_flags_active=.*PIPER_CORE_TRANSITIONS.*")

        # And ensure the canonical [STATE] lines still appear
        self.assertIn("[STATE] available_states=", out)
        self.assertIn("[STATE] available_events=", out)


if __name__ == "__main__":
    unittest.main()

