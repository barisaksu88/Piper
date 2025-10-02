# scripts/tests/test_transition_plan_subset.py
# Ensures router transitions (when flag is ON) are a subset of core.transition_plan.INTENDED

import os
import importlib
import unittest

# Enable transitions before importing router
os.environ["PIPER_CORE_TRANSITIONS"] = "1"

try:
    from scripts.core import router, transition_plan
    from scripts.core.state_defs import CoreState, EventType
except ModuleNotFoundError:
    from core import router, transition_plan  # type: ignore
    from core.state_defs import CoreState, EventType  # type: ignore

# Reload to pick up env flag consistently
router = importlib.reload(router)

class TestRouterTransitionsSubset(unittest.TestCase):
    def test_router_transitions_are_subset_of_plan(self):
        # Gather intended transitions as a set of (from_state, event_type, to_state)
        intended = set(transition_plan.INTENDED)

        # Peek at router's active transitions (private, but OK in tests)
        active = getattr(router, "_TRANSITIONS", {})
        for (from_state, event_type), handler in active.items():
            # Derive to_state by invoking the handler on the documented 'from_state'
            # Using None payload for now; handlers should handle it or ignore.
            to_state = handler(from_state, None)
            triplet = (from_state, event_type, to_state)
            self.assertIn(
                triplet, intended,
                f"Router transition {triplet} not in transition_plan.INTENDED"
            )

if __name__ == "__main__":
    unittest.main()

