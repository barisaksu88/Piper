# scripts/tests/test_timers_idle.py
# Non-runtime test for core.timers.IdleTimer (names only).

import time
import unittest

try:
    from scripts.core.timers import IdleTimer
except ModuleNotFoundError:
    from core.timers import IdleTimer  # cwd=C:\Piper\scripts

class TestIdleTimer(unittest.TestCase):
    def test_expired_and_reset(self):
        t = IdleTimer(timeout_s=1, on_timeout=lambda: None)
        self.assertFalse(t.expired())
        time.sleep(1.1)
        self.assertTrue(t.expired())
        t.reset()
        self.assertFalse(t.expired())

if __name__ == "__main__":
    unittest.main()

