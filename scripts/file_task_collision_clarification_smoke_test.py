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


TARGET_NAME = "important_notes"


@dataclass(frozen=True)
class FileTaskCollisionClarificationTurnReport:
    name: str
    assistant_text: str
    timed_out: bool
    duration_s: float


@dataclass(frozen=True)
class FileTaskCollisionClarificationSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    tasks_payload: dict
    file_exists: bool
    misrouted_override_trace: bool
    turns: list[FileTaskCollisionClarificationTurnReport]


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


def run_smoke(*, timeout: float, keep_data_copy: bool) -> FileTaskCollisionClarificationSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    workspace = harness.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / TARGET_NAME
    if target.exists():
        target.unlink()

    boot = harness.start()
    harness.chat_state.clear()
    turns: list[FileTaskCollisionClarificationTurnReport] = []

    for name, text in (
        ("create_file", "Create a file called important_notes."),
        ("create_task", "Also add a task called important_notes."),
        ("delete_target", "Delete important_notes."),
        ("clarify_file", "I mean the file."),
    ):
        result = harness.send_text(text, timeout_s=timeout)
        turns.append(
            FileTaskCollisionClarificationTurnReport(
                name=name,
                assistant_text=result.assistant_text,
                timed_out=result.timed_out,
                duration_s=result.duration_s,
            )
        )

    tasks_payload = _read_json(data_state_path(harness.data_dir, "tasks.json"))
    file_exists = target.exists()
    planner_debug_path = harness.data_dir / "debug" / "planner_debug.txt"
    try:
        planner_debug_text = planner_debug_path.read_text(encoding="utf-8")
    except Exception:
        planner_debug_text = ""
    harness.close()

    final_reply = turns[-1].assistant_text.lower() if turns else ""
    misrouted_override_trace = (
        "explicitly requested to override the dependency" in planner_debug_text.lower()
        or "overriding the active task dependency" in planner_debug_text.lower()
    )
    success = (
        bool(boot.ready)
        and all(not turn.timed_out for turn in turns)
        and str(tasks_payload.get(TARGET_NAME) or "") == "pending"
        and file_exists
        and TARGET_NAME in final_reply
        and ("task" in final_reply or "active" in final_reply or "dependency" in final_reply or "override" in final_reply)
        and "deleted" not in final_reply
        and "removed" not in final_reply
        and "successfully located" not in final_reply
        and "stage 1" not in final_reply
        and not misrouted_override_trace
    )
    return FileTaskCollisionClarificationSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        tasks_payload=tasks_payload,
        file_exists=file_exists,
        misrouted_override_trace=bool(misrouted_override_trace),
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify same-name file/task clarification stays grounded in dependency state instead of falsely deleting the file."
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
        print(f"TASKS: {json.dumps(report.tasks_payload, ensure_ascii=False)}")
        print(f"FILE_EXISTS: {report.file_exists}")
        for turn in report.turns:
            print(f"{turn.name}: timed_out={turn.timed_out} duration_s={turn.duration_s}")
            print(f"  assistant={turn.assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
