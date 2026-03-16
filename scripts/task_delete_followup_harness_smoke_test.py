from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import data_state_path  # noqa: E402
from AGENTS.harness.session import PiperHarness  # noqa: E402


def _read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    harness = PiperHarness(
        persist_turns=False,
        enable_memory_learning=True,
        isolated_data=True,
        keep_data_copy=True,
    )
    boot = harness.start()
    if not boot.ready:
        print(json.dumps({"success": False, "boot": boot.__dict__}, indent=2, ensure_ascii=False))
        harness.close()
        return 1

    turns = []
    try:
        for text in (
            "Add a task to buy milk.",
            "What tasks do I have right now?",
            "Please remove that from the tasks.",
            "What tasks do I have now?",
        ):
            result = harness.send_text(text, timeout_s=180.0)
            turns.append(
                {
                    "user_text": result.user_text,
                    "assistant_text": result.assistant_text,
                    "errors": [
                        event["payload"] for event in result.ui_events if event.get("kind") == "error"
                    ],
                    "tasks": _read_json(data_state_path(harness.data_dir, "tasks.json")) or {},
                }
            )
    finally:
        harness.close()

    errors = [err for turn in turns for err in turn.get("errors", [])]
    final_tasks = turns[-1]["tasks"] if turns else {}
    success = (
        not errors
        and turns[1]["assistant_text"] == "Pending tasks: buy milk."
        and turns[3]["assistant_text"] == "No pending tasks."
        and final_tasks == {}
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "turns": turns,
                "kept_data_dir": str(harness.kept_data_dir or ""),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
