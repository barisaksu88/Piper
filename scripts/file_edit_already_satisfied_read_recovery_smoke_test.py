from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.executor import StageExecutor  # noqa: E402


@dataclass(frozen=True)
class FileEditAlreadySatisfiedReadRecoveryReport:
    success: bool
    recovered: bool
    file_verdict: str
    checker_reason: str


class _Brain:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace


def run_smoke() -> FileEditAlreadySatisfiedReadRecoveryReport:
    stage = {
        "stage_goal": 'Locate the workspace file that best matches "my memory", remove the exact text "worms" from its contents, and save the updated file.',
        "stage_type": "FILE_WORK",
        "success_condition": 'A matching file is identified and no longer contains "worms".',
        "allowed_tools": ["FILE_OP"],
        "context": [
            "The workspace root is '.'.",
            'The requested document reference is "my memory".',
            "Keep all other file content unchanged unless the requested text removal requires a local formatting cleanup.",
        ],
    }

    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        text_dir = workspace / "text_files"
        text_dir.mkdir(parents=True, exist_ok=True)
        memory_dump = text_dir / "memory_dump.txt"
        session_memory = text_dir / "session_memory.txt"
        memory_dump.write_text(
            "Current tasks:\n- Get passport\n\nOther notes:\n- Flight was a test, no actual flight today\n",
            encoding="utf-8",
        )
        session_memory.write_text("Previous chat history with Baris.", encoding="utf-8")

        executor = StageExecutor(
            llm_client=None,
            agent_brain=_Brain(workspace),
            img_gen=None,
            boot_mgr=None,
            ui_queue=Queue(),
        )
        read_result = {
            "tool": "FILE_OP",
            "status": "EXECUTED",
            "summary": "Read 2 files.",
            "action": "read_many",
            "requested_paths": ["text_files/memory_dump.txt", "text_files/session_memory.txt"],
            "files": {
                "text_files/memory_dump.txt": memory_dump.read_text(encoding="utf-8"),
                "text_files/session_memory.txt": session_memory.read_text(encoding="utf-8"),
            },
            "evidence_files": ["text_files/memory_dump.txt", "text_files/session_memory.txt"],
        }
        executor._last_successful_tool_name = "FILE_OP"
        executor._last_successful_tool_result = read_result
        executor._append_exact_file_read_note_from_result(read_result)
        recovered = executor._auto_finish_verified_current_state_after_successful_read(stage)

    verdict = executor._last_file_verdict
    checker_reason = ""
    for entry in reversed(executor.scratchpad):
        if "FILE_CHECKER_REASON:" in entry:
            checker_reason = entry.split("FILE_CHECKER_REASON:", 1)[1].splitlines()[0].strip()
            break
    success = bool(recovered) and verdict == "VERIFIED" and "already absent" in checker_reason.lower()
    return FileEditAlreadySatisfiedReadRecoveryReport(
        success=success,
        recovered=bool(recovered),
        file_verdict=verdict,
        checker_reason=checker_reason,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify edit stages auto-finish from current-state verification when the target text is already absent after inspection."
    )


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
