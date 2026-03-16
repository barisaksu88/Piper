from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from memory import KnowledgeManager  # noqa: E402
from memory.state_owner import SharedStateOwner  # noqa: E402
from memory.transient_state import TransientStateManager  # noqa: E402


class _DummyLLM:
    def generate(self, messages, temperature: float = 0.1):
        return "{}"


@dataclass(frozen=True)
class TransientStateManagerReport:
    success: bool
    situational_keys: list[str]
    intent_keys: list[str]
    post_reconcile_intent_keys: list[str]
    situational_render: str
    intent_render: str
    world_state_render: str
    improving_activity_present: bool
    project_focus_present: bool


def run_smoke() -> TransientStateManagerReport:
    with tempfile.TemporaryDirectory(prefix="piper-transient-state-") as tmp:
        data_dir = Path(tmp)
        owner = SharedStateOwner.for_data_dir(data_dir)
        knowledge_mgr = KnowledgeManager(
            data_dir,
            _DummyLLM(),
            world_model_store=owner.world_model_store,
            knowledge_store=owner.knowledge_store,
        )

        now_ts = int(time.time())
        graph = owner.world_model_store.load_graph()
        root = graph["nodes"][graph["root_entity_id"]]
        root["attributes"]["pending_dentist_appointment_sentiment"] = [
            {"value": "hesitant_to_schedule", "expires_at": now_ts + 86400, "updated_at": now_ts}
        ]
        root["attributes"]["current_activity"] = [
            {"value": "debugging Piper", "expires_at": now_ts + 86400, "updated_at": now_ts}
        ]
        owner.world_model_store.save_graph(graph)

        manager = TransientStateManager(
            situational_store=owner.situational_state_store,
            intent_store=owner.intent_state_store,
            knowledge_mgr=knowledge_mgr,
        )

        manager.ingest_user_turn("Maybe I should create a fuzzy words code.")
        manager.ingest_user_turn("Maybe I should create a fuzzy words code.")
        manager.ingest_user_turn("Could you create a fuzzy words code for me?")
        manager.ingest_user_turn("I'm hungry and tired.")
        manager.ingest_user_turn("We're watching Ironman.")
        manager.ingest_user_turn("I'm trying to say your name, but it's just not picking up.")
        manager.ingest_user_turn("Please remember that I'm working on improving piper, which is you.")
        manager.ingest_user_turn("My biggest project is currently working on you, Piper.")
        manager.ingest_user_turn("I need to bike loot tomorrow.")

        situational = manager.list_situational_entries()
        intents = manager.list_intent_entries()
        situational_render = manager.render_situational_state("movie fuzzy voice")
        intent_render = manager.render_intent_state("fuzzy")
        manager.reconcile_operational_change(
            kind="event",
            action="remove",
            name="I need to bike loot",
            source_text="Not bike loot, ride my bike.",
        )
        post_reconcile_intents = manager.list_intent_entries()
        world_state_render = knowledge_mgr.render_prompt_state("", max_entities=6)
        improving_activity_present = any(
            "working on improving piper" in str(entry.get("value") or "").lower()
            for entry in situational.values()
        )
        project_focus_present = any(
            "working on you, piper" in str(entry.get("value") or "").lower()
            for entry in situational.values()
        )

        success = (
            "intent:create-a-fuzzy-words-code" in intents
            and len([key for key in intents if key == "intent:create-a-fuzzy-words-code"]) == 1
            and any(entry.get("value") == "hesitant_to_schedule" for entry in situational.values())
            and any(entry.get("value") == "debugging Piper" for entry in situational.values())
            and any(entry.get("value") == "hungry" for entry in situational.values())
            and any(entry.get("value") == "tired" for entry in situational.values())
            and any("not picking up" in str(entry.get("value") or "") for entry in situational.values())
            and improving_activity_present
            and project_focus_present
            and "intent:bike-loot-tomorrow" in intents
            and "intent:bike-loot-tomorrow" not in post_reconcile_intents
            and "[SITUATIONAL STATE]" in situational_render
            and "[INTENT STATE]" in intent_render
            and "create a fuzzy words code" in intent_render.lower()
            and "Pending Dentist Appointment Sentiment" not in world_state_render
            and "debugging Piper" not in world_state_render
            and "improving piper" not in world_state_render.lower()
            and "working on you, piper" not in world_state_render.lower()
        )

        return TransientStateManagerReport(
            success=bool(success),
            situational_keys=sorted(situational.keys()),
            intent_keys=sorted(intents.keys()),
            post_reconcile_intent_keys=sorted(post_reconcile_intents.keys()),
            situational_render=situational_render,
            intent_render=intent_render,
            world_state_render=world_state_render,
            improving_activity_present=improving_activity_present,
            project_focus_present=project_focus_present,
        )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
