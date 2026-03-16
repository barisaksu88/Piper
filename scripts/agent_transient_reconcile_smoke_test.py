from __future__ import annotations

import json
import types
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if "memory.brain" not in sys.modules:
    stub = types.ModuleType("memory.brain")

    class _DummyBrain:
        def remember(self, text: str, metadata=None):
            return None

    stub.get_brain = lambda data_dir: _DummyBrain()
    sys.modules["memory.brain"] = stub

from core.agent import AgentBrain  # noqa: E402
from memory.state_owner import SharedStateOwner  # noqa: E402
from memory.transient_state import TransientStateManager  # noqa: E402


@dataclass(frozen=True)
class AgentTransientReconcileSmokeReport:
    success: bool
    add_intent_removed: bool
    remove_intent_removed: bool
    complete_intent_removed: bool
    add_result: str
    remove_result: str
    complete_result: str


def run_smoke() -> AgentTransientReconcileSmokeReport:
    with tempfile.TemporaryDirectory(prefix="piper-agent-transient-") as tmp:
        data_dir = Path(tmp)
        owner = SharedStateOwner.for_data_dir(data_dir)
        transient_mgr = TransientStateManager(
            situational_store=owner.situational_state_store,
            intent_store=owner.intent_state_store,
        )
        agent = AgentBrain(
            data_dir,
            state_owner=owner,
            transient_state_manager=transient_mgr,
        )

        transient_mgr.ingest_user_turn("I want to ride my bike tomorrow.")
        add_result = agent.exec_add_event("ride my bike on tomorrow")
        add_intent_removed = "intent:ride-my-bike-tomorrow" not in transient_mgr.list_intent_entries()

        agent.exec_add_event("I need to bike loot on tomorrow")
        transient_mgr.ingest_user_turn("I need to bike loot tomorrow.")
        remove_result = agent.exec_remove_event("I need to bike loot")
        remove_intent_removed = "intent:bike-loot-tomorrow" not in transient_mgr.list_intent_entries()

        agent.exec_add_event("to wash my car on tomorrow")
        transient_mgr.ingest_user_turn("I need to wash my car tomorrow.")
        complete_result = agent.exec_complete_event("to wash my car")
        complete_intent_removed = "intent:wash-my-car-tomorrow" not in transient_mgr.list_intent_entries()

    success = (
        add_result.startswith("Event scheduled:")
        and remove_result.startswith("Event removed:")
        and complete_result.startswith("Event completed and archived:")
        and add_intent_removed
        and remove_intent_removed
        and complete_intent_removed
    )
    return AgentTransientReconcileSmokeReport(
        success=bool(success),
        add_intent_removed=bool(add_intent_removed),
        remove_intent_removed=bool(remove_intent_removed),
        complete_intent_removed=bool(complete_intent_removed),
        add_result=add_result,
        remove_result=remove_result,
        complete_result=complete_result,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
