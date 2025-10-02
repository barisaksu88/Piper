# scripts/tests/test_core_event_flow.py
# Non-runtime test: EventQueue + Event + router.process_event end-to-end (no transitions).

import unittest

try:
    from scripts.core.state_defs import CoreState, EventType
    from scripts.core.event_queue import EventQueue
    from scripts.core.events import publish, try_dequeue, Event
    from scripts.core.router import process_event
except ModuleNotFoundError:
    from core.state_defs import CoreState, EventType
    from core.event_queue import EventQueue
    from core.events import publish, try_dequeue, Event
    from core.router import process_event

class TestCoreEventFlow(unittest.TestCase):
    def test_flow_noop(self):
        q = EventQueue()
        publish(q, EventType.WakeDetected, payload={"keyword": "piper"})

        evt = try_dequeue(q)
        self.assertIsInstance(evt, Event)
        self.assertEqual(evt.type, EventType.WakeDetected)

        state = CoreState.SLEEPING
        next_state = process_event(state, evt.type, evt.payload)
        self.assertEqual(next_state, state)  # no transitions defined yet

if __name__ == "__main__":
    unittest.main()

