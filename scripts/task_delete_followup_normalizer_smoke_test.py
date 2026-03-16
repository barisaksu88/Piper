from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.route_normalizer import normalize_route_decision  # noqa: E402


def _base_decision() -> dict:
    return {
        "decision": "TASK",
        "card": {
            "goal": "Remove the user fact 'tasks' from memory",
            "context": [],
            "stages": [
                {
                    "stage_goal": "Remove the durable user fact 'tasks' from memory",
                    "stage_type": "MEMORY_WORK",
                    "success_condition": "Knowledge store no longer contains the fact tasks",
                    "allowed_tools": ["REMOVE_KNOWLEDGE"],
                }
            ],
        },
    }


def main() -> int:
    single_history = [
        {"role": "assistant", "content": "Pending tasks: buy milk."},
        {
            "role": "system",
            "content": (
                "[LATEST_RUNTIME_CONTEXT]\n"
                "[LATEST_SYSTEM_EVENT]\n"
                "[FINAL_STAGE_OUTCOME]\n"
                "=== STAGE 1 OUTCOME ===\n"
                "RESULT: TASKS LISTED\n"
                "LAST_LOG: Pending tasks: buy milk."
            ),
        },
    ]
    single = normalize_route_decision(
        _base_decision(),
        "Please remove that from the tasks.",
        single_history,
    )
    single_stage = dict((single.get("card") or {}).get("stages", [{}])[0])

    multi_history = [
        {"role": "assistant", "content": "Pending tasks: buy milk, buy bread."},
        {
            "role": "system",
            "content": (
                "[LATEST_RUNTIME_CONTEXT]\n"
                "[LATEST_SYSTEM_EVENT]\n"
                "[FINAL_STAGE_OUTCOME]\n"
                "=== STAGE 1 OUTCOME ===\n"
                "RESULT: TASKS LISTED\n"
                "LAST_LOG: Pending tasks: buy milk, buy bread."
            ),
        },
    ]
    ambiguous = normalize_route_decision(
        _base_decision(),
        "Please remove that from the tasks.",
        multi_history,
    )
    ambiguous_stage = dict((ambiguous.get("card") or {}).get("stages", [{}])[0])

    success = (
        single.get("decision") == "TASK"
        and single_stage.get("stage_type") == "TASK_EVENT_WORK"
        and list(single_stage.get("allowed_tools") or []) == ["DELETE_TASK"]
        and "buy milk" in str(single_stage.get("stage_goal") or "").lower()
        and ambiguous.get("decision") == "TASK"
        and ambiguous_stage.get("stage_type") == "CHAT"
        and "which task" in str(ambiguous_stage.get("stage_goal") or "").lower()
    )
    print(
        json.dumps(
            {
                "success": bool(success),
                "single_stage": single_stage,
                "ambiguous_stage": ambiguous_stage,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
