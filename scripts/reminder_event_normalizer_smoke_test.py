from __future__ import annotations

import datetime as dt
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.routing.route_dates import extract_date_phrase, resolve_date_phrase  # noqa: E402
from core.routing.route_normalizer import normalize_route_decision  # noqa: E402


@dataclass(frozen=True)
class ReminderEventNormalizerReport:
    success: bool
    date_phrase: str
    resolved_date: str
    next_tuesday_resolved: str
    next_tuesday_expected: str
    normalized_goal: str
    normalized_stage_goal: str
    allowed_tools: list[str]
    retry_goal: str
    retry_stage_goal: str
    retry_allowed_tools: list[str]


def run_smoke() -> ReminderEventNormalizerReport:
    decision = {
        "decision": "TASK",
        "card": {
            "goal": "Complete the task described by the latest user update",
            "context": [
                "The user's car insurance ends on the 25th.",
                "The user needs to get a new yearly insurance policy.",
            ],
            "stages": [
                {
                    "stage_goal": "Mark the task 'Oh yeah, something important. My insurance company told me that my cars insurance will end on the 25th, so remind me to get a new yearly insurance for that' as completed and archive it",
                    "stage_type": "TASK_EVENT_WORK",
                    "success_condition": "Active task is removed from the list and the completion is archived as memory",
                    "allowed_tools": ["COMPLETE_TASK"],
                }
            ],
        },
    }
    user_msg = "Oh yeah, something important. My insurance company told me that my cars insurance will end on the 25th, so remind me to get a new yearly insurance for that."

    normalized = normalize_route_decision(decision, user_msg)
    card = dict(normalized.get("card") or {})
    stages = [dict(stage) for stage in (card.get("stages") or []) if isinstance(stage, dict)]
    stage = stages[0] if stages else {}

    retry_normalized = normalize_route_decision(
        {
            "decision": "TASK",
            "card": {
                "goal": "Successfully log the car insurance renewal reminder for the 25th into the user's task/event system.",
                "context": [
                    "The user's insurance reminder failed previously.",
                    "The user wants to retry the same dated reminder request.",
                ],
                "stages": [
                    {
                        "stage_goal": "Re-attempt to create a new event in the system for the car insurance renewal on the 25th.",
                        "stage_type": "TASK_EVENT_WORK",
                        "success_condition": "The system confirms the event has been successfully added to the calendar or task list.",
                    }
                ],
            },
        },
        "Try again.",
        recent_history=[
            {
                "role": "system",
                "content": (
                    "[LATEST_RUNTIME_CONTEXT]\n"
                    "Previous route: TASK\n"
                    f"Previous user request: {user_msg}\n"
                    "Task goal: Successfully log the car insurance renewal reminder for the 25th into the user's task/event system.\n"
                    "Execution status: FAILED / INCOMPLETE\n"
                    "Runtime note: Task not found: insurance renewal\n"
                    "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict."
                ),
                "hidden": True,
            }
        ],
    )
    retry_card = dict(retry_normalized.get("card") or {})
    retry_stages = [dict(stage) for stage in (retry_card.get("stages") or []) if isinstance(stage, dict)]
    retry_stage = retry_stages[0] if retry_stages else {}

    date_phrase = extract_date_phrase(user_msg)
    resolved_date = resolve_date_phrase(date_phrase)
    today = dt.date.today()
    next_tuesday_delta = (1 - today.weekday()) % 7
    if next_tuesday_delta == 0:
        next_tuesday_delta = 7
    next_tuesday_expected = (today + dt.timedelta(days=next_tuesday_delta)).strftime("%Y-%m-%d")
    next_tuesday_resolved = resolve_date_phrase("next Tuesday")
    goal = str(card.get("goal") or "")
    stage_goal = str(stage.get("stage_goal") or "")
    allowed_tools = list(stage.get("allowed_tools") or [])
    retry_goal = str(retry_card.get("goal") or "")
    retry_stage_goal = str(retry_stage.get("stage_goal") or "")
    retry_allowed_tools = list(retry_stage.get("allowed_tools") or [])
    success = (
        normalized.get("decision") == "TASK"
        and goal == f"Add an event for get a new yearly insurance on {resolved_date}"
        and stage_goal == f"Schedule the event 'get a new yearly insurance' for {resolved_date}"
        and allowed_tools == ["ADD_EVENT"]
        and retry_normalized.get("decision") == "TASK"
        and retry_goal == f"Add an event for get a new yearly insurance on {resolved_date}"
        and retry_stage_goal == f"Schedule the event 'get a new yearly insurance' for {resolved_date}"
        and retry_allowed_tools == ["ADD_EVENT"]
        and next_tuesday_resolved == next_tuesday_expected
    )
    return ReminderEventNormalizerReport(
        success=bool(success),
        date_phrase=date_phrase,
        resolved_date=resolved_date,
        next_tuesday_resolved=next_tuesday_resolved,
        next_tuesday_expected=next_tuesday_expected,
        normalized_goal=goal,
        normalized_stage_goal=stage_goal,
        allowed_tools=allowed_tools,
        retry_goal=retry_goal,
        retry_stage_goal=retry_stage_goal,
        retry_allowed_tools=retry_allowed_tools,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
