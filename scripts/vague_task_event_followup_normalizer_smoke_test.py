from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.route_normalizer import normalize_route_decision  # noqa: E402


@dataclass(frozen=True)
class VagueTaskEventFollowupNormalizerReport:
    success: bool
    task_followup_goal: str
    task_followup_tools: list[str]
    event_followup_goal: str
    event_followup_tools: list[str]
    plural_task_goal: str
    plural_task_tools: list[str]
    plural_task_stage_count: int
    plural_list_stage_count: int


def _normalize(user_msg: str, runtime_context: str) -> dict:
    return normalize_route_decision(
        {"decision": "CHAT"},
        user_msg,
        recent_history=[
            {"role": "system", "content": runtime_context, "hidden": True},
            {"role": "user", "content": user_msg},
        ],
    )


def run_smoke() -> VagueTaskEventFollowupNormalizerReport:
    task_runtime = (
        "[LATEST_RUNTIME_CONTEXT]\n"
        "Previous route: TASK\n"
        "Previous user request: Add a task to buy bread.\n"
        "Task goal: Add a new task to the user's list to buy bread.\n"
        "Execution status: TASK ADDED\n"
        "Runtime note: Task added: buy bread\n"
        "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
    )
    event_runtime = (
        "[LATEST_RUNTIME_CONTEXT]\n"
        "Previous route: TASK\n"
        "Previous user request: Add an event smoke beta appointment on today.\n"
        "Task goal: Add an event for smoke beta appointment on 2026-03-13\n"
        "Execution status: EVENT SCHEDULED\n"
        "Runtime note: Event scheduled: smoke beta appointment on 2026-03-13\n"
        "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
    )

    task_followup = _normalize("I did it.", task_runtime)
    event_followup = _normalize("I went to it.", event_runtime)
    plural_task_followup = _normalize("Done the shopping, remove them all.", task_runtime)
    plural_task_from_list = normalize_route_decision(
        {"decision": "TASK", "card": {"goal": "", "context": [], "stages": []}},
        "Done the shopping, remove them all.",
        recent_history=[
            {"role": "assistant", "content": "Pending tasks: buy milk, buy bread."},
            {"role": "user", "content": "Done the shopping, remove them all."},
        ],
    )

    task_stage = dict((task_followup.get("card") or {}).get("stages", [{}])[0])
    event_stage = dict((event_followup.get("card") or {}).get("stages", [{}])[0])
    plural_task_stage = dict((plural_task_followup.get("card") or {}).get("stages", [{}])[0])
    plural_list_stages = [dict(stage) for stage in ((plural_task_from_list.get("card") or {}).get("stages") or [])]

    success = (
        task_followup.get("decision") == "TASK"
        and "buy bread" in str((task_followup.get("card") or {}).get("goal") or "").lower()
        and list(task_stage.get("allowed_tools") or []) == ["COMPLETE_TASK", "LIST_TASKS"]
        and event_followup.get("decision") == "TASK"
        and "smoke beta appointment" in str((event_followup.get("card") or {}).get("goal") or "").lower()
        and list(event_stage.get("allowed_tools") or []) == ["COMPLETE_EVENT", "LIST_EVENTS"]
        and plural_task_followup.get("decision") == "TASK"
        and "buy bread" in str((plural_task_followup.get("card") or {}).get("goal") or "").lower()
        and list(plural_task_stage.get("allowed_tools") or []) == ["COMPLETE_TASK", "LIST_TASKS"]
        and plural_task_from_list.get("decision") == "TASK"
        and len(plural_list_stages) == 2
        and all(list(stage.get("allowed_tools") or []) == ["COMPLETE_TASK", "LIST_TASKS"] for stage in plural_list_stages)
        and "buy milk" in str(plural_list_stages[0].get("stage_goal") or "").lower()
        and "buy bread" in str(plural_list_stages[1].get("stage_goal") or "").lower()
    )

    return VagueTaskEventFollowupNormalizerReport(
        success=bool(success),
        task_followup_goal=str((task_followup.get("card") or {}).get("goal") or ""),
        task_followup_tools=list(task_stage.get("allowed_tools") or []),
        event_followup_goal=str((event_followup.get("card") or {}).get("goal") or ""),
        event_followup_tools=list(event_stage.get("allowed_tools") or []),
        plural_task_goal=str((plural_task_followup.get("card") or {}).get("goal") or ""),
        plural_task_tools=list(plural_task_stage.get("allowed_tools") or []),
        plural_task_stage_count=len((plural_task_followup.get("card") or {}).get("stages") or []),
        plural_list_stage_count=len(plural_list_stages),
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
