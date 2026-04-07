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


@dataclass(frozen=True)
class FileWorkStateIsolationTurnReport:
    name: str
    assistant_text: str
    timed_out: bool
    duration_s: float


@dataclass(frozen=True)
class FileWorkStateIsolationSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    temp_files: list[str]
    turns: list[FileWorkStateIsolationTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run_smoke(*, timeout: float, keep_data_copy: bool) -> FileWorkStateIsolationSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    data_state_path(harness.data_dir, "tasks.json").write_text(
        json.dumps({"test_task": "pending"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    data_state_path(harness.data_dir, "events.json").write_text("{}", encoding="utf-8")

    workspace = harness.data_dir / "workspace"
    target_dir = workspace / "temp_data"
    if target_dir.exists():
        for path in sorted(target_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        target_dir.rmdir()

    boot = harness.start()
    harness.chat_state.clear()
    turns: list[FileWorkStateIsolationTurnReport] = []

    for name, text in (
        ("file_work", 'Create a folder called "temp_data" and add 3 dummy files inside it.'),
        ("task_query", "What is my current task list?"),
    ):
        result = harness.send_text(text, timeout_s=timeout)
        turns.append(
            FileWorkStateIsolationTurnReport(
                name=name,
                assistant_text=result.assistant_text,
                timed_out=result.timed_out,
                duration_s=result.duration_s,
            )
        )

    temp_files = sorted(path.name for path in target_dir.glob("*") if path.is_file())
    harness.close()

    query_reply = turns[-1].assistant_text.lower() if turns else ""
    success = (
        bool(boot.ready)
        and all(not turn.timed_out for turn in turns)
        and temp_files == ["dummy1.txt", "dummy2.txt", "dummy3.txt"]
        and "test_task" in query_reply
        and "temp_data" not in query_reply
        and "dummy" not in query_reply
        and "created" not in query_reply
    )
    return FileWorkStateIsolationSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        temp_files=temp_files,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify FILE_WORK turns do not leak file-creation narration into a later task-list chat turn."
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
        print(f"TEMP_FILES: {report.temp_files}")
        for turn in report.turns:
            print(f"{turn.name}: timed_out={turn.timed_out} duration_s={turn.duration_s}")
            print(f"  assistant={turn.assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
