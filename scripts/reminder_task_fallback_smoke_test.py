from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
from config import data_state_path


@dataclass(frozen=True)
class ReminderTaskFallbackSmokeReport:
    success: bool
    user_id: str
    first_reply: str
    second_reply: str
    third_reply: str
    final_query_reply: str
    tasks_after_first: dict[str, str]
    tasks_after_third: dict[str, str]
    reminders: list[dict[str, object]]
    data_dir: str
    kept_data_dir: str | None


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_smoke(*, timeout: float, keep_data_copy: bool) -> ReminderTaskFallbackSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    try:
        boot = harness.start()
        harness._handle_command("/user Max")
        active_profile = harness.user_runtime.active_profile()
        user_data_dir = harness.user_runtime.current_user_data_dir()
        tasks_path = data_state_path(user_data_dir, "tasks.json")
        reminders_path = harness.data_dir / "reminders.json"

        first = harness.send_text("remind me to buy milk", timeout_s=timeout)
        tasks_after_first = dict((_read_json(tasks_path) or {}))

        second = harness.send_text("remind me to call mum at 5", timeout_s=timeout)
        third = harness.send_text("set it as a task", timeout_s=timeout)
        tasks_after_third = dict((_read_json(tasks_path) or {}))

        final_query = harness.send_text("what tasks do i have right now?", timeout_s=timeout)
        reminders = list((_read_json(reminders_path) or []))

        success = bool(
            boot.ready
            and active_profile.user_id == "max"
            and not first.timed_out
            and not second.timed_out
            and not third.timed_out
            and not final_query.timed_out
            and tasks_after_first.get("buy milk") == "pending"
            and tasks_after_third.get("buy milk") == "pending"
            and tasks_after_third.get("call mum") == "pending"
            and "buy milk" in str(final_query.assistant_text or "").lower()
            and "call mum" in str(final_query.assistant_text or "").lower()
            and not any("buy milk" in str(item.get("message") or "").lower() for item in reminders)
        )

        return ReminderTaskFallbackSmokeReport(
            success=bool(success),
            user_id=str(active_profile.user_id),
            first_reply=str(first.assistant_text or ""),
            second_reply=str(second.assistant_text or ""),
            third_reply=str(third.assistant_text or ""),
            final_query_reply=str(final_query.assistant_text or ""),
            tasks_after_first=tasks_after_first,
            tasks_after_third=tasks_after_third,
            reminders=reminders,
            data_dir=str(harness.data_dir),
            kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        )
    finally:
        harness.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify untimed reminder phrasing falls back to tasks and explicit task follow-up recovers correctly."
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-turn timeout in seconds.")
    parser.add_argument("--keep-data-copy", action="store_true", help="Preserve the isolated data copy for inspection.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    _configure_stdio()
    args = build_parser().parse_args()
    report = run_smoke(timeout=args.timeout, keep_data_copy=args.keep_data_copy)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"USER_ID: {report.user_id}")
        print(f"TASKS_AFTER_FIRST: {report.tasks_after_first}")
        print(f"TASKS_AFTER_THIRD: {report.tasks_after_third}")
        print(f"FINAL_QUERY_REPLY: {report.final_query_reply}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
