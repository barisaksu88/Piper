from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import data_state_path  # noqa: E402
from AGENTS.harness.session import PiperHarness  # noqa: E402


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ScenarioReport:
    name: str
    success: bool
    turns: list[dict[str, Any]]
    kept_data_dir: str


def _turn_record(result, data_dir: Path) -> dict[str, Any]:
    return {
        "user_text": result.user_text,
        "assistant_text": result.assistant_text,
        "errors": [event["payload"] for event in result.ui_events if event.get("kind") == "error"],
        "tasks": _read_json(data_state_path(data_dir, "tasks.json")) or {},
    }


def _run(turns: list[str]) -> tuple[list[dict[str, Any]], str]:
    harness = PiperHarness(
        persist_turns=False,
        enable_memory_learning=True,
        isolated_data=True,
        keep_data_copy=True,
    )
    boot = harness.start()
    records: list[dict[str, Any]] = []
    if not boot.ready:
        harness.close()
        return ([{"boot_error": True, "boot": boot.__dict__}], "")
    try:
        data_state_path(harness.data_dir, "tasks.json").write_text("{}", encoding="utf-8")
        for text in turns:
            result = harness.send_text(text, timeout_s=180.0)
            records.append(_turn_record(result, harness.data_dir))
    finally:
        harness.close()
    return records, str(harness.kept_data_dir or "")


def _exact_flow_success(turns: list[dict[str, Any]]) -> bool:
    if len(turns) != 5:
        return False
    errors = [err for turn in turns for err in turn.get("errors", [])]
    return (
        not errors
        and turns[2]["tasks"] == {"by bread": "pending"}
        and turns[3]["tasks"] == {}
        and turns[4]["assistant_text"] == "No pending tasks."
    )


def _multi_list_success(turns: list[dict[str, Any]]) -> bool:
    if len(turns) != 5:
        return False
    errors = [err for turn in turns for err in turn.get("errors", [])]
    cleanup_reply = str(turns[3].get("assistant_text") or "").lower()
    return (
        not errors
        and "buy milk" in str(turns[2]["assistant_text"] or "").lower()
        and "buy bread" in str(turns[2]["assistant_text"] or "").lower()
        and turns[3]["tasks"] == {}
        and ("now clear" in cleanup_reply or "successfully archived" in cleanup_reply or "removed" in cleanup_reply)
        and turns[4]["assistant_text"] == "No pending tasks."
    )


def main() -> int:
    exact_turns, exact_kept = _run(
        [
            "Add a task to buy milk.",
            "Please remove that from the tasks.",
            "Add by bread to the tasks.",
            "Done the shopping, remove them all.",
            "What tasks do I have now?",
        ]
    )
    multi_turns, multi_kept = _run(
        [
            "Add a task to buy milk.",
            "Add a task to buy bread.",
            "What tasks do I have right now?",
            "Done the shopping, remove them all.",
            "What tasks do I have now?",
        ]
    )

    reports = [
        ScenarioReport(
            name="exact_shopping_followup",
            success=_exact_flow_success(exact_turns),
            turns=exact_turns,
            kept_data_dir=exact_kept,
        ),
        ScenarioReport(
            name="multi_task_visible_list_followup",
            success=_multi_list_success(multi_turns),
            turns=multi_turns,
            kept_data_dir=multi_kept,
        ),
    ]
    success = all(report.success for report in reports)
    print(
        json.dumps(
            {
                "success": bool(success),
                "reports": [asdict(report) for report in reports],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
