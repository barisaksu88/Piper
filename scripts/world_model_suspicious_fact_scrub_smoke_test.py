from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from memory import KnowledgeManager  # noqa: E402
from memory.state_owner import SharedStateOwner  # noqa: E402


class _DummyLLM:
    def generate(self, messages, temperature: float = 0.1):
        return "{}"


@dataclass(frozen=True)
class WorldModelSuspiciousFactScrubSmokeReport:
    success: bool
    world_contains_suspicious_key: bool
    knowledge_contains_suspicious_key: bool
    world_contains_questionish_value: bool
    knowledge_contains_questionish_value: bool


def run_smoke() -> WorldModelSuspiciousFactScrubSmokeReport:
    with tempfile.TemporaryDirectory(prefix="piper-world-model-scrub-") as tmp:
        data_dir = Path(tmp)
        owner = SharedStateOwner.for_data_dir(data_dir)
        graph = owner.world_model_store.load_graph()
        root = graph["nodes"][graph["root_entity_id"]]
        root["attributes"]["i'm_working_on_improving_piper,_which"] = [
            {"value": "you", "expires_at": None, "updated_at": 1773443771}
        ]
        root["attributes"]["personality_trait"] = [
            {"value": "max, what is yours", "expires_at": None, "updated_at": 1775358423}
        ]
        owner.world_model_store.save_graph(graph)

        KnowledgeManager(
            data_dir,
            _DummyLLM(),
            world_model_store=owner.world_model_store,
            knowledge_store=owner.knowledge_store,
        )

        world_payload = json.loads(owner.world_model_store.path.read_text(encoding="utf-8"))
        knowledge_payload = json.loads(owner.knowledge_store.path.read_text(encoding="utf-8"))
        world_blob = json.dumps(world_payload, ensure_ascii=False).lower()
        knowledge_blob = json.dumps(knowledge_payload, ensure_ascii=False).lower()

        report = WorldModelSuspiciousFactScrubSmokeReport(
            success=(
                "i'm_working_on_improving_piper,_which" not in world_blob
                and "working on improving piper, which" not in knowledge_blob
                and "max, what is yours" not in world_blob
                and "max, what is yours" not in knowledge_blob
            ),
            world_contains_suspicious_key="i'm_working_on_improving_piper,_which" in world_blob,
            knowledge_contains_suspicious_key="working on improving piper, which" in knowledge_blob,
            world_contains_questionish_value="max, what is yours" in world_blob,
            knowledge_contains_questionish_value="max, what is yours" in knowledge_blob,
        )
        return report


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
