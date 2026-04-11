from __future__ import annotations

import json
import sys
import tempfile
import types
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


@dataclass(frozen=True)
class AgentFileOpParserSmokeReport:
    success: bool
    status: str
    action: str
    files: list[str]


def run_smoke() -> AgentFileOpParserSmokeReport:
    with tempfile.TemporaryDirectory(prefix="piper-agent-file-op-parser-") as tmp:
        data_dir = Path(tmp)
        workspace = data_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "grocery_list.txt").write_text("Apples\nBananas\n", encoding="utf-8")
        nested = workspace / "text_files"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "grocery_list.txt").write_text("milk\neggs\nbread\n", encoding="utf-8")

        owner = SharedStateOwner.for_data_dir(data_dir)
        agent = AgentBrain(data_dir, state_owner=owner)
        action = agent.parse_and_execute(
            '[FILE_OP: {"action":"read_many","paths":["grocery_list.txt","text_files/grocery_list.txt"]}]'
        )

        result = action.execute_result if isinstance(action.execute_result, dict) else {}
        files = sorted(str(item) for item in (result.get("files") or []))
        success = (
            action.tag == "FILE_OP"
            and str(result.get("status", "")).upper() == "EXECUTED"
            and str(result.get("action", "")) == "read_many"
            and files == ["grocery_list.txt", "text_files/grocery_list.txt"]
        )
        return AgentFileOpParserSmokeReport(
            success=bool(success),
            status=str(result.get("status", "")),
            action=str(result.get("action", "")),
            files=files,
        )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
