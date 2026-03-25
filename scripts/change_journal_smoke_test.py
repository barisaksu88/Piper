from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

import sys

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.engines.change_journal import ChangeJournal
from core.routing.route_normalizer import detect_route_interceptor
from tools.workspace_runtime import WorkspaceToolRuntime


@dataclass(frozen=True)
class ChangeJournalSmokeReport:
    success: bool
    interceptor_ok: bool
    overwrite_restored: bool
    create_removed: bool
    entry_count: int
    latest_undone: bool


def run_smoke() -> ChangeJournalSmokeReport:
    with tempfile.TemporaryDirectory(prefix="piper-change-journal-") as tmp:
        data_dir = Path(tmp) / "data"
        workspace = data_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        runtime = WorkspaceToolRuntime(workspace)
        journal = ChangeJournal(data_dir / "change_journal.json")

        existing_path = workspace / "text_files" / "undo_existing.txt"
        existing_path.parent.mkdir(parents=True, exist_ok=True)
        existing_path.write_text("before", encoding="utf-8")

        overwrite_tag = '[FILE_OP] {"action":"write_text","path":"text_files/undo_existing.txt","content":"after"} [/FILE_OP]'
        overwrite_capture = journal.prepare_file_op_capture_from_tool_tag(overwrite_tag, workspace)
        overwrite_result = runtime.exec_file_op('{"action":"write_text","path":"text_files/undo_existing.txt","content":"after"}')
        overwrite_op = journal.finalize_file_op_capture(overwrite_capture, overwrite_result)
        journal.record_turn(
            turn_id="overwrite-turn",
            user_msg="overwrite existing file",
            task_goal="Overwrite undo_existing.txt",
            task_success=True,
            operations=[overwrite_op] if overwrite_op else [],
        )
        overwrite_undo = journal.undo_latest(workspace)
        overwrite_restored = (
            str(overwrite_undo.get("status") or "") == "VERIFIED"
            and existing_path.read_text(encoding="utf-8") == "before"
        )

        create_tag = '[FILE_OP] {"action":"write_text","path":"undo_tree/nested/new_file.txt","content":"created"} [/FILE_OP]'
        create_capture = journal.prepare_file_op_capture_from_tool_tag(create_tag, workspace)
        create_result = runtime.exec_file_op('{"action":"write_text","path":"undo_tree/nested/new_file.txt","content":"created"}')
        create_op = journal.finalize_file_op_capture(create_capture, create_result)
        journal.record_turn(
            turn_id="create-turn",
            user_msg="create nested file",
            task_goal="Create undo_tree/nested/new_file.txt",
            task_success=True,
            operations=[create_op] if create_op else [],
        )
        create_undo = journal.undo_latest(workspace)
        create_removed = (
            str(create_undo.get("status") or "") == "VERIFIED"
            and not (workspace / "undo_tree" / "nested" / "new_file.txt").exists()
            and not (workspace / "undo_tree" / "nested").exists()
            and not (workspace / "undo_tree").exists()
        )

        entries = journal.load_entries()
        latest = journal.peek_latest_entry() or {}
        interceptor_ok = bool(detect_route_interceptor("undo that"))
        success = interceptor_ok and overwrite_restored and create_removed and len(entries) == 2 and bool(str(latest.get("undone_at") or "").strip())
        return ChangeJournalSmokeReport(
            success=bool(success),
            interceptor_ok=bool(interceptor_ok),
            overwrite_restored=bool(overwrite_restored),
            create_removed=bool(create_removed),
            entry_count=len(entries),
            latest_undone=bool(str(latest.get("undone_at") or "").strip()),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test reversible FILE_OP journaling and undo snapshot restore.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"INTERCEPTOR_OK: {report.interceptor_ok}")
        print(f"OVERWRITE_RESTORED: {report.overwrite_restored}")
        print(f"CREATE_REMOVED: {report.create_removed}")
        print(f"ENTRY_COUNT: {report.entry_count}")
        print(f"LATEST_UNDONE: {report.latest_undone}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
