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


ORIGINAL_LINES = ["Authorized", "Access Granted"]
APPENDED_LINES = ORIGINAL_LINES + ["Connection Closed"]


@dataclass(frozen=True)
class FileAppendReadbackUndoTurnReport:
    name: str
    assistant_text: str
    timed_out: bool
    duration_s: float
    file_exists: bool
    file_lines: list[str]


@dataclass(frozen=True)
class FileAppendReadbackUndoSmokeReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    turns: list[FileAppendReadbackUndoTurnReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _snapshot(path) -> tuple[bool, list[str]]:
    if not path.exists():
        return False, []
    return True, path.read_text(encoding="utf-8").splitlines()


def _contains_lines_in_order(text: str, lines: list[str]) -> bool:
    lowered = str(text or "").lower()
    cursor = 0
    for line in lines:
        needle = line.lower()
        idx = lowered.find(needle, cursor)
        if idx < 0:
            return False
        cursor = idx + len(needle)
    return True


def run_smoke(*, timeout: float, keep_data_copy: bool) -> FileAppendReadbackUndoSmokeReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    for state_name in ("tasks.json", "events.json"):
        data_state_path(harness.data_dir, state_name).write_text("{}", encoding="utf-8")

    workspace = harness.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "security.log"
    if target.exists():
        target.unlink()

    boot = harness.start()
    harness.chat_state.clear()
    turns: list[FileAppendReadbackUndoTurnReport] = []

    for name, text in (
        ("create", 'Create a file called security.log. Line 1 should be "Authorized". Line 2 should be "Access Granted".'),
        ("append", 'Update security.log. Add a line "Connection Closed" at the end. Do not change lines 1 or 2.'),
        ("readback", "Read it back exactly."),
        ("undo", "Undo that."),
    ):
        result = harness.send_text(text, timeout_s=timeout)
        file_exists, file_lines = _snapshot(target)
        turns.append(
            FileAppendReadbackUndoTurnReport(
                name=name,
                assistant_text=result.assistant_text,
                timed_out=result.timed_out,
                duration_s=result.duration_s,
                file_exists=file_exists,
                file_lines=file_lines,
            )
        )

    harness.close()

    readback_reply = turns[2].assistant_text if len(turns) > 2 else ""
    undo_reply = turns[3].assistant_text.lower() if len(turns) > 3 else ""
    success = (
        bool(boot.ready)
        and all(not turn.timed_out for turn in turns)
        and turns[1].file_exists
        and turns[1].file_lines == APPENDED_LINES
        and turns[2].file_exists
        and turns[2].file_lines == APPENDED_LINES
        and _contains_lines_in_order(readback_reply, APPENDED_LINES)
        and turns[3].file_exists
        and turns[3].file_lines == ORIGINAL_LINES
        and ("undo" in undo_reply or "undid" in undo_reply or "reverted" in undo_reply or "restored" in undo_reply)
    )
    return FileAppendReadbackUndoSmokeReport(
        ready=bool(boot.ready),
        success=bool(success),
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        turns=turns,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify constrained appends can be read back exactly and undo restores the pre-append content."
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
        for turn in report.turns:
            print(f"{turn.name}: timed_out={turn.timed_out} duration_s={turn.duration_s}")
            print(f"  assistant={turn.assistant_text}")
            print(f"  file_exists={turn.file_exists} file_lines={turn.file_lines}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
