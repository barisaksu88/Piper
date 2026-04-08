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
class FileAppendConstraintsTurnReport:
    name: str
    assistant_text: str
    timed_out: bool
    duration_s: float


@dataclass(frozen=True)
class FileAppendConstraintsSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    final_content: str | None
    final_lines: list[str]
    turns: list[FileAppendConstraintsTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run_smoke(*, timeout: float, keep_data_copy: bool) -> FileAppendConstraintsSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    workspace = harness.data_dir / "workspace"
    target = workspace / "security.log"
    if target.exists():
        target.unlink()

    boot = harness.start()
    harness.chat_state.clear()
    turns: list[FileAppendConstraintsTurnReport] = []

    for name, text in (
        ("create", 'Create a file called security.log. Line 1 should be "Authorized". Line 2 should be "Access Granted".'),
        ("append", 'Update security.log. Add a line "Connection Closed" at the end. Do not change lines 1 or 2.'),
    ):
        result = harness.send_text(text, timeout_s=timeout)
        turns.append(
            FileAppendConstraintsTurnReport(
                name=name,
                assistant_text=result.assistant_text,
                timed_out=result.timed_out,
                duration_s=result.duration_s,
            )
        )

    final_content = target.read_text(encoding="utf-8") if target.exists() else None
    harness.close()

    final_lines = final_content.splitlines() if final_content is not None else []
    success = (
        bool(boot.ready)
        and all(not turn.timed_out for turn in turns)
        and final_lines == ["Authorized", "Access Granted", "Connection Closed"]
    )
    return FileAppendConstraintsSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        final_content=final_content,
        final_lines=final_lines,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify append-style file edits preserve existing lines and do not fall into an incomplete retry loop."
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
        print(f"DATA_DIR: {report.data_dir}")
        if report.kept_data_dir:
            print(f"KEPT_DATA_DIR: {report.kept_data_dir}")
        print(f"FINAL_LINES: {report.final_lines}")
        for turn in report.turns:
            print(f"{turn.name}: timed_out={turn.timed_out} duration_s={turn.duration_s}")
            print(f"  assistant={turn.assistant_text}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
