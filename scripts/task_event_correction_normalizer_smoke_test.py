from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.routing.route_normalizer import normalize_route_decision  # noqa: E402


@dataclass(frozen=True)
class TaskEventCorrectionNormalizerReport:
    success: bool
    normalized_decision: str
    normalized_goal: str


def run_smoke() -> TaskEventCorrectionNormalizerReport:
    prior_route = {
        "decision": "TASK",
        "card": {
            "goal": "Complete the event 'dentist appointment'",
            "context": [
                "The user has a scheduled dentist appointment.",
                "The assistant previously acted as if the appointment had not been booked yet.",
            ],
            "stages": [
                {
                    "stage_goal": "Mark the event 'dentist appointment' as completed and archive it",
                    "stage_type": "TASK_EVENT_WORK",
                    "success_condition": "Active event is removed from the list and the completion is archived as memory",
                    "allowed_tools": ["COMPLETE_EVENT"],
                }
            ],
        },
    }

    normalized = normalize_route_decision(
        prior_route,
        "Fix your calendar, I already got an appointment.",
        recent_history=[
            {"role": "user", "content": "How are you doing?"},
            {
                "role": "assistant",
                "content": "You're hesitating to schedule the dentist appointment.",
            },
            {"role": "user", "content": "Fix your calendar, I already got an appointment."},
        ],
    )

    normalized_decision = str(normalized.get("decision") or "")
    normalized_goal = str((normalized.get("card") or {}).get("goal") or "")
    success = normalized_decision == "CHAT"
    return TaskEventCorrectionNormalizerReport(
        success=bool(success),
        normalized_decision=normalized_decision,
        normalized_goal=normalized_goal,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
