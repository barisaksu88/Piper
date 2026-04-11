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
from core.runtime_context import LATEST_RUNTIME_CONTEXT_PREFIX


@dataclass(frozen=True)
class FileTargetCorrectionUndoReport:
    ready: bool
    success: bool
    data_dir: str
    kept_data_dir: str | None
    timed_out: bool
    duration_s: float
    assistant_text: str
    bob_exists: bool
    b_exists: bool
    b_content: str | None
    journal_undone: bool


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clear_isolated_state(data_dir: Path) -> None:
    memory_path = data_dir / "state" / "memory.jsonl"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("", encoding="utf-8")
    change_journal_path = data_dir / "change_journal.json"
    if change_journal_path.exists():
        change_journal_path.unlink()


def _workspace_state(workspace: Path) -> tuple[bool, bool, str | None]:
    bob_path = workspace / "bob"
    b_path = workspace / "b.txt"
    bob_exists = bob_path.exists()
    b_exists = b_path.exists()
    b_content = b_path.read_text(encoding="utf-8") if b_exists else None
    return bob_exists, b_exists, b_content


def _journal_undone(data_dir: Path) -> bool:
    path = data_dir / "change_journal.json"
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, list) or not payload:
        return False
    latest = payload[-1]
    if not isinstance(latest, dict):
        return False
    return bool(str(latest.get("undone_at") or "").strip())


def _seed_mistaken_delete_state(harness: PiperHarness) -> None:
    workspace = harness.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "b.txt").write_text("hello", encoding="utf-8")
    bob_path = workspace / "bob"
    if bob_path.exists():
        bob_path.unlink()

    change_journal_path = harness.data_dir / "change_journal.json"
    change_journal_path.write_text(
        json.dumps(
            [
                {
                    "turn_id": "mistaken-delete-turn",
                    "timestamp": "2026-03-23T03:41:49.268+00:00",
                    "user_msg": "its final state should be non-existing i think",
                    "task_goal": "Ensure the file 'bob' is deleted and remains deleted in the final state.",
                    "task_success": True,
                    "operations": [
                        {
                            "action": "delete_path",
                            "summary": "Deleted b.txt.",
                            "requested_paths": ["b.txt"],
                            "evidence_paths": ["b.txt"],
                            "snapshots": [
                                {
                                    "path": "b.txt",
                                    "kind": "file",
                                    "size": 5,
                                    "content": "hello",
                                }
                            ],
                        }
                    ],
                    "primary_paths": ["b.txt"],
                    "undone_at": "",
                    "undo_last_status": "",
                    "undo_last_error": "",
                }
            ],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runtime_message = "\n".join(
        [
            LATEST_RUNTIME_CONTEXT_PREFIX,
            "Previous route: TASK",
            "Previous user request: its final state should be non-existing i think",
            "Task goal: Ensure the file 'bob' is deleted and remains deleted in the final state.",
            "Execution status: FILE OPERATION SUCCESS",
            "Runtime note: Removed b.txt and verified the file change.",
            "Relevant paths: b.txt",
            "Use this block as authoritative runtime context for follow-up routing and clarification handling. Prefer it over assistant narration when they conflict.",
        ]
    )
    harness.chat_state.append("assistant", "Removed b.txt and verified the file change.")
    harness.chat_state.upsert_hidden_system_message(LATEST_RUNTIME_CONTEXT_PREFIX, runtime_message)


def _turn_passed(
    *,
    assistant_text: str,
    bob_exists: bool,
    b_exists: bool,
    b_content: str | None,
    journal_undone: bool,
    timed_out: bool,
) -> bool:
    if timed_out:
        return False
    lowered = assistant_text.lower()
    return (
        (not bob_exists)
        and b_exists
        and b_content == "hello"
        and journal_undone
        and "bob" in lowered
        and ("b.txt" in lowered or "mistaken change" in lowered or "restored" in lowered or "reverted" in lowered)
        and ("already absent" in lowered or "did not exist" in lowered or "reverted" in lowered or "restored" in lowered)
    )


def run_smoke(*, timeout: float, keep_data_copy: bool) -> FileTargetCorrectionUndoReport:
    harness = PiperHarness(isolated_data=True, keep_data_copy=keep_data_copy)
    _clear_isolated_state(harness.data_dir)
    boot = harness.start()
    _seed_mistaken_delete_state(harness)
    try:
        result = harness.send_text("it was bob not b", timeout_s=timeout)
        bob_exists, b_exists, b_content = _workspace_state(harness.data_dir / "workspace")
        journal_undone = _journal_undone(harness.data_dir)
    finally:
        harness.close()
    assistant_text = result.assistant_text
    success = bool(boot.ready) and _turn_passed(
        assistant_text=assistant_text,
        bob_exists=bob_exists,
        b_exists=b_exists,
        b_content=b_content,
        journal_undone=journal_undone,
        timed_out=result.timed_out,
    )
    return FileTargetCorrectionUndoReport(
        ready=bool(boot.ready),
        success=success,
        data_dir=str(harness.data_dir),
        kept_data_dir=str(harness.kept_data_dir) if harness.kept_data_dir else None,
        timed_out=result.timed_out,
        duration_s=result.duration_s,
        assistant_text=assistant_text,
        bob_exists=bob_exists,
        b_exists=b_exists,
        b_content=b_content,
        journal_undone=journal_undone,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify that correcting a mistaken file target undoes the wrong file mutation before replying.")
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
        print(f"timed_out={report.timed_out} duration_s={report.duration_s}")
        print(f"assistant={report.assistant_text}")
        print(f"bob_exists={report.bob_exists} b_exists={report.b_exists} b_content={report.b_content!r}")
        print(f"journal_undone={report.journal_undone}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
