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


@dataclass(frozen=True)
class UndoTurnReport:
    name: str
    timed_out: bool
    duration_s: float
    assistant_text: str
    file_exists: bool
    file_content: str | None
    passed: bool


@dataclass(frozen=True)
class UndoFlowSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    turns: list[UndoTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_memory(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")
    change_journal_path = data_dir / "change_journal.json"
    if change_journal_path.exists():
        change_journal_path.unlink()


def _workspace_state(workspace: Path) -> tuple[bool, str | None]:
    target = workspace / "text_files" / "undo_alpha.txt"
    if not target.exists():
        return False, None
    return True, target.read_text(encoding="utf-8")


def _turn_passed(name: str, assistant_text: str, file_exists: bool, file_content: str | None, timed_out: bool) -> bool:
    if timed_out:
        return False
    if name == "create":
        lowered = assistant_text.lower()
        return (
            file_exists
            and file_content == "alpha beta gamma"
            and ("updated" in lowered or "verified" in lowered or "created" in lowered)
        )
    if name == "undo":
        lowered = assistant_text.lower()
        return (not file_exists) and ("reverted" in lowered or "restored" in lowered)
    return False


def run_smoke(*, timeout: float, keep_data_copy: bool) -> UndoFlowSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_memory(harness.data_dir)
    workspace = harness.data_dir / "workspace"
    target = workspace / "text_files" / "undo_alpha.txt"
    if target.exists():
        target.unlink()
    boot = harness.start()
    turns: list[UndoTurnReport] = []
    try:
        for name, text in (
            ("create", "In the workspace, create the text file text_files/undo_alpha.txt with the exact contents: alpha beta gamma"),
            ("undo", "undo that"),
        ):
            result = harness.send_text(text, timeout_s=timeout)
            exists, content = _workspace_state(workspace)
            turns.append(
                UndoTurnReport(
                    name=name,
                    timed_out=result.timed_out,
                    duration_s=result.duration_s,
                    assistant_text=result.assistant_text,
                    file_exists=exists,
                    file_content=content,
                    passed=_turn_passed(name, result.assistant_text, exists, content, result.timed_out),
                )
            )
    finally:
        harness.close()
    return UndoFlowSmokeReport(
        ready=bool(boot.ready),
        success=bool(boot.ready) and all(turn.passed for turn in turns),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an end-to-end undo flow smoke through the isolated Piper harness.")
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
        for turn in report.turns:
            print(f"{turn.name}: passed={turn.passed} timed_out={turn.timed_out} duration_s={turn.duration_s}")
            print(f"  assistant={turn.assistant_text}")
            print(f"  file_exists={turn.file_exists} file_content={turn.file_content!r}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
