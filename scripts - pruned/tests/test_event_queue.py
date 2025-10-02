# scripts/tests/test_event_queue.py
# Non-runtime unit test for EventQueue. Safe: not imported by the app.

import unittest

try:
    from scripts.core.event_queue import EventQueue
except ModuleNotFoundError:
    from core.event_queue import EventQueue  # cwd=C:\Piper\scripts

class TestEventQueue(unittest.TestCase):
    def test_enqueue_dequeue(self):
        q = EventQueue()
        self.assertEqual(len(q), 0)

        q.enqueue("A")
        q.enqueue("B")
        self.assertEqual(len(q), 2)

        self.assertEqual(q.dequeue(), "A")
        self.assertEqual(q.dequeue(), "B")
        self.assertIsNone(q.dequeue())
        self.assertEqual(len(q), 0)

if __name__ == "__main__":
    unittest.main()

