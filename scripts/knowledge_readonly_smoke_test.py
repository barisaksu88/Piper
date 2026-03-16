from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.environment_service import EnvironmentService  # noqa: E402
from core.instructions_loader import InstructionLoader  # noqa: E402
from core.operational_state_service import OperationalStateService  # noqa: E402
from core.prompt_context import PromptContextService  # noqa: E402
from memory.documents import DocumentMemoryManager  # noqa: E402
from memory.state_owner import SharedStateOwner  # noqa: E402


class _DummyBrain:
    workspace = ROOT_DIR / "data" / "workspace"

    def recall(self, user_msg: str, n_results: int = 5):
        return []


class _DummyKnowledge:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}

    def load(self):
        return dict(self._data)

    def upsert_fact(self, key: str, value: str) -> bool:
        self._data[str(key)] = {"value": str(value)}
        return True

    def remove_fact(self, key: str) -> bool:
        return self._data.pop(str(key), None) is not None

    def render_prompt_state(self, query: str, *, max_entities: int = 6) -> str:
        return ""

    def render_situational_state(self, query: str = "", *, max_items: int = 4) -> str:
        return ""

    def list_for_display(self) -> str:
        if not self._data:
            return "No world model stored."
        lines = ["[WORLD STATE]"]
        for key, payload in self._data.items():
            value = str((payload or {}).get("value") or "").strip()
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)


@dataclass(frozen=True)
class KnowledgeReadonlySmokeReport:
    success: bool
    knowledge_answer: str
    forgotten_answer: str
    unrelated_answer: str
    todo_answer: str
    profile_summary_answer: str


def run_smoke() -> KnowledgeReadonlySmokeReport:
    with tempfile.TemporaryDirectory(prefix="piper-knowledge-readonly-") as tmp:
        data_dir = Path(tmp)
        (data_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (data_dir / "prompts" / "instructions.txt").write_text("", encoding="utf-8")
        owner = SharedStateOwner.for_data_dir(data_dir)
        knowledge = _DummyKnowledge()
        knowledge.upsert_fact("favorite drink", "coffee")
        owner.event_store.add("dentist appointment", "2026-03-24")
        owner.task_store.add("buy milk", "pending")

        service = PromptContextService(
            instruction_loader=InstructionLoader(data_dir / "prompts" / "instructions.txt"),
            environment_service=EnvironmentService(owner),
            operational_state_service=OperationalStateService(owner),
            knowledge_mgr=knowledge,
            brain=_DummyBrain(),
            document_memory=DocumentMemoryManager(data_dir),
        )

        knowledge_answer = service.build_readonly_state_answer("What do you know about my favorite drink?")
        todo_answer = service.build_readonly_state_answer("What's on my to-do list?")
        profile_summary_answer = service.build_readonly_state_answer("Tell me everything you know about me.")
        knowledge.remove_fact("favorite drink")
        forgotten_answer = service.build_readonly_state_answer("Do you remember my favorite drink now?")
        unrelated_answer = service.build_readonly_state_answer("What do you know about my favorite drink?")

    success = (
        knowledge_answer == "Your favorite drink is coffee."
        and todo_answer == "Pending tasks: buy milk."
        and "- favorite drink: coffee" in profile_summary_answer
        and "Upcoming events: dentist appointment on 2026-03-24." in profile_summary_answer
        and forgotten_answer == "I do not have a stored favorite drink."
        and unrelated_answer == "I do not have a stored favorite drink."
    )
    return KnowledgeReadonlySmokeReport(
        success=bool(success),
        knowledge_answer=knowledge_answer,
        forgotten_answer=forgotten_answer,
        unrelated_answer=unrelated_answer,
        todo_answer=todo_answer,
        profile_summary_answer=profile_summary_answer,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
