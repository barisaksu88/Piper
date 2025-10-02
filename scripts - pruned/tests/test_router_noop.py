# scripts/tests/test_router_noop.py
# Non-runtime test: validate router.process_event with empty transitions.

import unittest

try:
    from scripts.core.state_defs import CoreState, EventType
    from scripts.core.event_queue import EventQueue
    from scripts.core.events import Event, publish, try_dequeue
    from scripts.core.router import process_event
except ModuleNotFoundError:
    from core.state_defs import CoreState, EventType
    from core.event_queue import EventQueue
    from core.events import Event, publish, try_dequeue
    from core.router import process_event

class TestRouterNoop(unittest.TestCase):
    def test_enqueue_dequeue_and_process(self):
        q = EventQueue()
        # Publish an event (WakeDetected) with no transition defined
        publish(q, EventType.WakeDetected, payload="hello")

        evt = try_dequeue(q)
        self.assertIsInstance(evt, Event)
        self.assertEqual(evt.type, EventType.WakeDetected)

        # Process event: should return the SAME state (since transitions empty)
        current = CoreState.SLEEPING
        next_state = process_event(current, evt.type, evt.payload)
        self.assertEqual(next_state, current)

if __name__ == "__main__":
    unittest.main()

