from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness


CREATE_TURN = "Create a file called test_notes.txt with the content 'hello'"
DELETE_TURN = "Delete test notes"


@dataclass(frozen=True)
class DeleteCaseReport:
    timed_out: bool
    assistant_text: str
    file_exists_after_turn: bool
    passed: bool


@dataclass(frozen=True)
class DeleteFollowupSmokeReport:
    ready: bool
    success: bool
    followup_data_dir: str
    fresh_data_dir: str
    followup_case: DeleteCaseReport
    fresh_case: DeleteCaseReport


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")


def _passed_delete_case(assistant_text: str, *, timed_out: bool, file_exists_after_turn: bool) -> bool:
    text = str(assistant_text or "").strip().lower()
    return (
        not timed_out
        and not file_exists_after_turn
        and "knowledge" not in text
        and "memory" not in text
    )


def _run_followup_case(*, timeout: float, keep_data_copy: bool) -> tuple[bool, str, DeleteCaseReport]:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    workspace = harness.data_dir / "workspace"
    target_path = workspace / "test_notes.txt"
    if target_path.exists():
        target_path.unlink()
    boot = harness.start()
    try:
        create_result = harness.send_text(CREATE_TURN, timeout_s=timeout)
        delete_result = harness.send_text(DELETE_TURN, timeout_s=timeout)
        file_exists_after_turn = target_path.exists()
        report = DeleteCaseReport(
            timed_out=bool(create_result.timed_out or delete_result.timed_out),
            assistant_text=str(delete_result.assistant_text or "").strip(),
            file_exists_after_turn=bool(file_exists_after_turn),
            passed=bool(target_path.exists() is False) and _passed_delete_case(
                delete_result.assistant_text,
                timed_out=bool(create_result.timed_out or delete_result.timed_out),
                file_exists_after_turn=bool(file_exists_after_turn),
            ),
        )
    finally:
        harness.close()
    return bool(boot.ready), str(harness.data_dir), report


def _run_fresh_case(*, timeout: float, keep_data_copy: bool) -> tuple[bool, str, DeleteCaseReport]:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    workspace = harness.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target_path = workspace / "test_notes.txt"
    target_path.write_text("hello", encoding="utf-8")
    boot = harness.start()
    try:
        delete_result = harness.send_text(DELETE_TURN, timeout_s=timeout)
        file_exists_after_turn = target_path.exists()
        report = DeleteCaseReport(
            timed_out=bool(delete_result.timed_out),
            assistant_text=str(delete_result.assistant_text or "").strip(),
            file_exists_after_turn=bool(file_exists_after_turn),
            passed=_passed_delete_case(
                delete_result.assistant_text,
                timed_out=bool(delete_result.timed_out),
                file_exists_after_turn=bool(file_exists_after_turn),
            ),
        )
    finally:
        harness.close()
    return bool(boot.ready), str(harness.data_dir), report


def run_smoke(*, timeout: float, keep_data_copy: bool) -> DeleteFollowupSmokeReport:
    followup_ready, followup_data_dir, followup_case = _run_followup_case(timeout=timeout, keep_data_copy=keep_data_copy)
    fresh_ready, fresh_data_dir, fresh_case = _run_fresh_case(timeout=timeout, keep_data_copy=keep_data_copy)
    return DeleteFollowupSmokeReport(
        ready=bool(followup_ready and fresh_ready),
        success=bool(followup_ready and fresh_ready and followup_case.passed and fresh_case.passed),
        followup_data_dir=followup_data_dir,
        fresh_data_dir=fresh_data_dir,
        followup_case=followup_case,
        fresh_case=fresh_case,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify that natural file-delete requests stay in FILE_WORK both as follow-ups and from a fresh session.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-turn timeout in seconds.")
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
        print(f"FOLLOWUP_DATA_DIR: {report.followup_data_dir}")
        print(f"FRESH_DATA_DIR: {report.fresh_data_dir}")
        print(f"FOLLOWUP_DELETE_ASSISTANT: {report.followup_case.assistant_text}")
        print(f"FOLLOWUP_FILE_EXISTS_AFTER_TURN: {report.followup_case.file_exists_after_turn}")
        print(f"FRESH_DELETE_ASSISTANT: {report.fresh_case.assistant_text}")
        print(f"FRESH_FILE_EXISTS_AFTER_TURN: {report.fresh_case.file_exists_after_turn}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
