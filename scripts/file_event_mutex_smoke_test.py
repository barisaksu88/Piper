from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from AGENTS.harness.session import PiperHarness
from config import data_state_path
from core.routing.route_dates import resolve_date_phrase


@dataclass(frozen=True)
class FileEventMutexTurnReport:
    name: str
    assistant_text: str
    timed_out: bool
    duration_s: float


@dataclass(frozen=True)
class FileEventMutexSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    expected_date: str
    events_payload: dict
    charlie_exists: bool
    turns: list[FileEventMutexTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _read_json(path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def run_smoke(*, timeout: float, keep_data_copy: bool) -> FileEventMutexSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    workspace = harness.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    charlie_path = workspace / "charlie.txt"
    charlie_path.write_text("charlie-body\n", encoding="utf-8")

    expected_date = resolve_date_phrase("next Tuesday") or "next Tuesday"
    boot = harness.start()
    harness.chat_state.clear()
    turns: list[FileEventMutexTurnReport] = []

    for name, text in (
        ("schedule", "Schedule an event for next Tuesday to review the file charlie.txt."),
        ("delete", "Delete charlie.txt."),
    ):
        result = harness.send_text(text, timeout_s=timeout)
        turns.append(
            FileEventMutexTurnReport(
                name=name,
                assistant_text=result.assistant_text,
                timed_out=result.timed_out,
                duration_s=result.duration_s,
            )
        )

    events_payload = _read_json(data_state_path(harness.data_dir, "events.json"))
    charlie_exists = charlie_path.exists()
    harness.close()

    delete_reply = turns[1].assistant_text.lower() if len(turns) > 1 else ""
    success = (
        bool(boot.ready)
        and all(not turn.timed_out for turn in turns)
        and str(events_payload.get("review the file charlie.txt") or "") == expected_date
        and charlie_exists
        and "event" in delete_reply
        and "charlie.txt" in delete_reply
    )
    return FileEventMutexSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        expected_date=expected_date,
        events_payload=events_payload,
        charlie_exists=charlie_exists,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify event-backed file references block deletes and preserve the file."
    )
    parser.add_argument("--timeout", type=float, default=240.0, help="Per-turn timeout in seconds.")
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
        print(f"READY: {report.ready}")
        print(f"SUCCESS: {report.success}")
        print(f"EXPECTED_DATE: {report.expected_date}")
        print(f"EVENTS: {json.dumps(report.events_payload, ensure_ascii=False)}")
        print(f"CHARLIE_EXISTS: {report.charlie_exists}")
        for turn in report.turns:
            print(f"{turn.name}: timed_out={turn.timed_out} duration_s={turn.duration_s}")
            print(f"  assistant={turn.assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
