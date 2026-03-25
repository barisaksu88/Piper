from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness


PREP_TURN = "Create a file called verify_test.txt with the content 'done'"
MISSING_EDIT_TURN = "Edit stent_file.txt, add a line saying 'test'"


@dataclass(frozen=True)
class MissingFileNoRerouteSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    assistant_text: str
    assistant_messages: list[str]
    status_history: list[str]
    missing_file_exists_after_turn: bool
    timed_out: bool
    duration_s: float


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_chat_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")


def _passes(*, assistant_text: str, assistant_messages: list[str], timed_out: bool, missing_file_exists_after_turn: bool) -> bool:
    text_l = str(assistant_text or "").strip().lower()
    honest_missing_failure = any(
        phrase in text_l
        for phrase in (
            "not found",
            "does not exist",
            "could not locate",
            "was not found",
        )
    )
    return (
        not timed_out
        and len(assistant_messages) == 1
        and not missing_file_exists_after_turn
        and honest_missing_failure
        and "updated stent_file.txt" not in text_l
        and "verified the file change" not in text_l
        and "engineering support" not in text_l
    )


def run_smoke(*, timeout: float, keep_data_copy: bool) -> MissingFileNoRerouteSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    target_path = harness.data_dir / "workspace" / "stent_file.txt"
    if target_path.exists():
        target_path.unlink()
    boot = harness.start()
    try:
        prep_result = harness.send_text(PREP_TURN, timeout_s=timeout)
        edit_result = harness.send_text(MISSING_EDIT_TURN, timeout_s=timeout)
        assistant_messages = [
            str(message.get("content") or "")
            for message in edit_result.messages
            if str(message.get("role") or "") == "assistant"
        ]
        missing_file_exists_after_turn = target_path.exists()
        timed_out = bool(prep_result.timed_out or edit_result.timed_out)
    finally:
        harness.close()
    return MissingFileNoRerouteSmokeReport(
        ready=bool(boot.ready),
        success=bool(boot.ready)
        and _passes(
            assistant_text=edit_result.assistant_text,
            assistant_messages=assistant_messages,
            timed_out=timed_out,
            missing_file_exists_after_turn=missing_file_exists_after_turn,
        ),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        assistant_text=str(edit_result.assistant_text or "").strip(),
        assistant_messages=assistant_messages,
        status_history=list(edit_result.status_history),
        missing_file_exists_after_turn=bool(missing_file_exists_after_turn),
        timed_out=timed_out,
        duration_s=round(prep_result.duration_s + edit_result.duration_s, 3),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify that a missing explicit file target fails once, does not reroute internally, and does not create the file.")
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
        print(f"READY: {report.ready}")
        print(f"SUCCESS: {report.success}")
        print(f"DATA_DIR: {report.data_dir}")
        if report.kept_data_dir:
            print(f"KEPT_DATA_DIR: {report.kept_data_dir}")
        print(f"ASSISTANT: {report.assistant_text}")
        print(f"ASSISTANT_MESSAGES: {report.assistant_messages}")
        print(f"MISSING_FILE_EXISTS_AFTER_TURN: {report.missing_file_exists_after_turn}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
