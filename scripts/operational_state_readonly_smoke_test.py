from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from memory.state_owner import SharedStateOwner  # noqa: E402
from core.operational_state_service import OperationalStateService  # noqa: E402


@dataclass(frozen=True)
class OperationalStateReadonlySmokeReport:
    success: bool
    task_answer: str
    any_task_answer: str
    todo_list_answer: str
    event_answer: str
    any_event_answer: str
    tomorrow_event_answer: str
    tomorrow_confirmation_answer: str
    appointment_when_answer: str
    no_tasks_or_events_answer: str
    there_should_be_events_answer: str
    countdown_answer: str
    clarified_countdown_answer: str


def run_smoke() -> OperationalStateReadonlySmokeReport:
    with tempfile.TemporaryDirectory(prefix="piper-opstate-") as tmp:
        root = Path(tmp)
        owner = SharedStateOwner.for_data_dir(root)
        today = dt.date.today()
        tomorrow = (today + dt.timedelta(days=1)).strftime("%Y-%m-%d")
        later = (today + dt.timedelta(days=30)).strftime("%Y-%m-%d")
        owner.task_store.add("buy milk", "pending")
        owner.event_store.add("dentist appointment", tomorrow)
        owner.event_store.add("house purchase fund", later)

        service = OperationalStateService(owner)
        task_answer = service.build_readonly_answer("What tasks do I have right now?")
        any_task_answer = service.build_readonly_answer("Any tasks?")
        todo_list_answer = service.build_readonly_answer("What's on my to-do list?")
        event_answer = service.build_readonly_answer("What events do I have scheduled?")
        any_event_answer = service.build_readonly_answer("Any events?")
        tomorrow_event_answer = service.build_readonly_answer("What's on my schedules for tomorrow?")
        tomorrow_confirmation_answer = service.build_readonly_answer("Do I have an event or no for tomorrow?")
        appointment_when_answer = service.build_readonly_answer("When is my appointment tomorrow?")
        no_tasks_or_events_answer = service.build_readonly_answer("No tasks or events.")
        there_should_be_events_answer = service.build_readonly_answer("There should be events now.")
        countdown_answer = service.build_readonly_answer("Tell me how many days my first upcoming event.")
        clarified_countdown_answer = service.build_readonly_answer(
            "But that's not what I asked. How many days are left to my first upcoming event?"
        )

    success = (
        task_answer == "Pending tasks: buy milk."
        and any_task_answer == "Pending tasks: buy milk."
        and todo_list_answer == "Pending tasks: buy milk."
        and f"dentist appointment on {tomorrow}" in event_answer
        and any_event_answer == f"Upcoming events: dentist appointment on {tomorrow}; house purchase fund on {later}."
        and tomorrow_event_answer == f"Upcoming events: dentist appointment on {tomorrow}."
        and tomorrow_confirmation_answer == f"Upcoming events: dentist appointment on {tomorrow}."
        and appointment_when_answer == f"Upcoming events: dentist appointment on {tomorrow}."
        and no_tasks_or_events_answer == f"Pending tasks: buy milk.\nUpcoming events: dentist appointment on {tomorrow}; house purchase fund on {later}."
        and there_should_be_events_answer == f"Upcoming events: dentist appointment on {tomorrow}; house purchase fund on {later}."
        and f"house purchase fund on {later}" in event_answer
        and "buy milk" not in event_answer
        and "dentist appointment" in countdown_answer
        and "Upcoming events:" not in countdown_answer
        and "dentist appointment" in clarified_countdown_answer
        and "Upcoming events:" not in clarified_countdown_answer
    )
    return OperationalStateReadonlySmokeReport(
        success=bool(success),
        task_answer=task_answer,
        any_task_answer=any_task_answer,
        todo_list_answer=todo_list_answer,
        event_answer=event_answer,
        any_event_answer=any_event_answer,
        tomorrow_event_answer=tomorrow_event_answer,
        tomorrow_confirmation_answer=tomorrow_confirmation_answer,
        appointment_when_answer=appointment_when_answer,
        no_tasks_or_events_answer=no_tasks_or_events_answer,
        there_should_be_events_answer=there_should_be_events_answer,
        countdown_answer=countdown_answer,
        clarified_countdown_answer=clarified_countdown_answer,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
