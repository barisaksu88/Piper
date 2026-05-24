from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
from core.engines.proactive_monitor import ProactiveMonitor
from core.services.reminders import (
    ReminderStore,
    build_proactive_trigger_message,
    parse_reminder_request,
)
from core.routing.route_normalizer import detect_route_interceptor


@dataclass(frozen=True)
class ProactiveMonitorSmokeReport:
    success: bool
    parse_ok: bool
    interceptor_ok: bool
    monitor_deferred: bool
    monitor_dispatched: bool
    reminder_turn_ok: bool
    proactive_turn_ok: bool
    reminder_assistant_text: str
    proactive_assistant_text: str
    data_dir: str
    kept_data_dir: str | None


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_harness_state(data_dir: Path) -> None:
    state_dir = data_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "memory.jsonl").write_text("", encoding="utf-8")
    for path in (
        data_dir / "reminders.json",
        data_dir / "conversation_summary.json",
    ):
        if path.exists():
            path.unlink()


def _run_monitor_unit_checks() -> tuple[bool, bool, bool, bool]:
    now_local = dt.datetime(2026, 3, 22, 14, 0, tzinfo=dt.timezone.utc).astimezone()
    parsed = parse_reminder_request("remind me to stretch in 20 minutes", now_local=now_local)
    interceptor = detect_route_interceptor("remind me to stretch in 20 minutes", [])
    parse_ok = bool(parsed.ok and parsed.message and parsed.fire_at_utc and parsed.fire_at_local)
    interceptor_ok = bool(interceptor and str(interceptor.get("kind") or "") == "REMINDER_SET")

    with tempfile.TemporaryDirectory(prefix="piper-proactive-monitor-") as tmp:
        reminders_path = Path(tmp) / "data" / "reminders.json"
        store = ReminderStore(reminders_path)
        entry = store.add(
            message="remind the user to stretch",
            fire_at_utc="2000-01-01T00:00:00Z",
        )
        dispatches: list[dict[str, object]] = []
        ready = {"value": False}

        def _dispatch(reminder: dict[str, object]) -> bool:
            dispatches.append(dict(reminder))
            return True

        monitor = ProactiveMonitor(
            reminders_path,
            poll_interval_s=0.05,
            can_dispatch=lambda: bool(ready["value"]),
            is_inflight=lambda reminder_id: False,
            dispatch_callback=_dispatch,
        )
        monitor.start()
        try:
            time.sleep(0.2)
            monitor_deferred = len(dispatches) == 0
            ready["value"] = True
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not dispatches:
                time.sleep(0.05)
            monitor_dispatched = bool(dispatches) and str(dispatches[0].get("id") or "") == str(entry.get("id") or "")
        finally:
            monitor.stop()

    return parse_ok, interceptor_ok, monitor_deferred, monitor_dispatched


def _assistant_mentions_reminder(text: str) -> bool:
    lowered = str(text or "").lower()
    return bool(lowered) and ("remind" in lowered or "reminder" in lowered)


def _run_harness_flow(*, timeout: float, keep_data_copy: bool) -> tuple[bool, bool, str, str, str, str | None]:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_harness_state(harness.data_dir)
    boot = harness.start()
    reminder_assistant_text = ""
    proactive_assistant_text = ""
    reminder_turn_ok = False
    proactive_turn_ok = False
    try:
        reminder_turn = harness.send_text("remind me to stretch in 20 minutes", timeout_s=timeout)
        reminder_assistant_text = reminder_turn.assistant_text
        reminders_path = harness.data_dir / "reminders.json"
        reminders = json.loads(reminders_path.read_text(encoding="utf-8")) if reminders_path.exists() else []
        reminder_turn_ok = bool(
            boot.ready
            and not reminder_turn.timed_out
            and reminders
            and not bool(reminders[-1].get("fired"))
            and _assistant_mentions_reminder(reminder_assistant_text)
        )

        if reminders:
            entry = dict(reminders[-1])
            entry["fire_at"] = "2000-01-01T00:00:00Z"
            reminders[-1] = entry
            reminders_path.write_text(json.dumps(reminders, indent=2, ensure_ascii=False), encoding="utf-8")

            msg_start = len(harness.chat_state.get_messages_snapshot())
            harness.chat_state.append_message(
                {
                    "role": "system",
                    "content": build_proactive_trigger_message(entry),
                    "hidden": True,
                }
            )
            harness._start_generation()
            timed_out = not harness._wait_for_idle(timeout_s=timeout, idle_grace_s=0.75)
            snapshot = harness.chat_state.get_messages_snapshot()
            new_messages = snapshot[msg_start:]
            assistant_messages = [message for message in new_messages if str(message.get("role") or "") == "assistant"]
            proactive_assistant_text = str(assistant_messages[-1].get("content") or "") if assistant_messages else ""
            refreshed = json.loads(reminders_path.read_text(encoding="utf-8")) if reminders_path.exists() else []
            fired = bool(refreshed and refreshed[-1].get("fired"))
            proactive_turn_ok = bool(
                not timed_out
                and fired
                and _assistant_mentions_reminder(proactive_assistant_text)
                and not any(str(message.get("role") or "") == "user" for message in new_messages)
            )
    finally:
        harness.close()
    return (
        reminder_turn_ok,
        proactive_turn_ok,
        reminder_assistant_text,
        proactive_assistant_text,
        str(harness.data_dir),
        str(harness.kept_data_dir) if harness.kept_data_dir else None,
    )


def run_smoke(*, timeout: float, keep_data_copy: bool) -> ProactiveMonitorSmokeReport:
    parse_ok, interceptor_ok, monitor_deferred, monitor_dispatched = _run_monitor_unit_checks()
    (
        reminder_turn_ok,
        proactive_turn_ok,
        reminder_assistant_text,
        proactive_assistant_text,
        data_dir,
        kept_data_dir,
    ) = _run_harness_flow(timeout=timeout, keep_data_copy=keep_data_copy)
    success = all(
        (
            parse_ok,
            interceptor_ok,
            monitor_deferred,
            monitor_dispatched,
            reminder_turn_ok,
            proactive_turn_ok,
        )
    )
    return ProactiveMonitorSmokeReport(
        success=bool(success),
        parse_ok=bool(parse_ok),
        interceptor_ok=bool(interceptor_ok),
        monitor_deferred=bool(monitor_deferred),
        monitor_dispatched=bool(monitor_dispatched),
        reminder_turn_ok=bool(reminder_turn_ok),
        proactive_turn_ok=bool(proactive_turn_ok),
        reminder_assistant_text=reminder_assistant_text,
        proactive_assistant_text=proactive_assistant_text,
        data_dir=data_dir,
        kept_data_dir=kept_data_dir,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test reminder routing, proactive monitor deferral, and synthetic reminder firing.")
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
        print(f"PARSE_OK: {report.parse_ok}")
        print(f"INTERCEPTOR_OK: {report.interceptor_ok}")
        print(f"MONITOR_DEFERRED: {report.monitor_deferred}")
        print(f"MONITOR_DISPATCHED: {report.monitor_dispatched}")
        print(f"REMINDER_TURN_OK: {report.reminder_turn_ok}")
        print(f"PROACTIVE_TURN_OK: {report.proactive_turn_ok}")
        print(f"DATA_DIR: {report.data_dir}")
        if report.kept_data_dir:
            print(f"KEPT_DATA_DIR: {report.kept_data_dir}")
        print(f"REMINDER_ASSISTANT: {report.reminder_assistant_text}")
        print(f"PROACTIVE_ASSISTANT: {report.proactive_assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
