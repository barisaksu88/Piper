from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

from AGENTS.harness.session import PiperHarness


CREATE_TURN = "Create a file called verify_test.txt with the content 'verified'"
EDIT_TURN = "Edit verify_test.txt — replace 'verified' with 'done' and also add a second line saying 'complete'"
EXPECTED_LINES = ["done", "complete"]


@dataclass(frozen=True)
class LiteralCompletionEditSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    assistant_text: str
    assistant_messages: list[str]
    status_history: list[str]
    file_content: str
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


def _passed(
    *,
    assistant_text: str,
    assistant_messages: list[str],
    file_content: str,
    timed_out: bool,
) -> bool:
    if timed_out:
        return False
    text_l = str(assistant_text or "").strip().lower()
    lines = [line.strip() for line in str(file_content or "").splitlines() if line.strip()]
    return (
        len(assistant_messages) == 1
        and lines == EXPECTED_LINES
        and bool(text_l)
        and "event not found" not in text_l
        and "failed" not in text_l
        and "unable to proceed" not in text_l
        and "task" not in text_l
        and "event" not in text_l
    )


def run_smoke(*, timeout: float, keep_data_copy: bool) -> LiteralCompletionEditSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_chat_memory(harness.data_dir)
    target_path = harness.data_dir / "workspace" / "verify_test.txt"
    boot = harness.start()
    try:
        create_result = harness.send_text(CREATE_TURN, timeout_s=timeout)
        edit_result = harness.send_text(EDIT_TURN, timeout_s=timeout)
        file_content = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
        assistant_messages = [
            str(message.get("content") or "")
            for message in edit_result.messages
            if str(message.get("role") or "") == "assistant"
        ]
        timed_out = bool(create_result.timed_out or edit_result.timed_out)
    finally:
        harness.close()
    return LiteralCompletionEditSmokeReport(
        ready=bool(boot.ready),
        success=bool(boot.ready)
        and _passed(
            assistant_text=edit_result.assistant_text,
            assistant_messages=assistant_messages,
            file_content=file_content,
            timed_out=timed_out,
        ),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        assistant_text=edit_result.assistant_text,
        assistant_messages=assistant_messages,
        status_history=list(edit_result.status_history),
        file_content=file_content,
        timed_out=timed_out,
        duration_s=round(create_result.duration_s + edit_result.duration_s, 3),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify compound file edits with literal text like 'done' and 'complete' stay in FILE_WORK and return one reply.")
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
        print(f"DATA_DIR: {report.data_dir}")
        if report.kept_data_dir:
            print(f"KEPT_DATA_DIR: {report.kept_data_dir}")
        print(f"ASSISTANT: {report.assistant_text}")
        print(f"ASSISTANT_MESSAGES: {report.assistant_messages}")
        print(f"FILE_CONTENT: {report.file_content!r}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
