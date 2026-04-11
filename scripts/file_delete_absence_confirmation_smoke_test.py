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

from core.file_checker import FileWorkChecker  # noqa: E402
from core.file_stage_policy import FileStagePolicy  # noqa: E402


@dataclass(frozen=True)
class FileDeleteAbsenceConfirmationReport:
    success: bool
    stage_requires_verification: bool
    current_verdict: str
    current_reason: str


class _Brain:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace


class _LLM:
    pass


def run_smoke() -> FileDeleteAbsenceConfirmationReport:
    stage = {
        "stage_goal": 'Find the workspace file that best matches "empty folders" and delete it if found.',
        "stage_type": "FILE_WORK",
        "success_condition": "A matching file is deleted, or the absence of any plausible file match is confirmed.",
        "allowed_tools": ["FILE_OP"],
        "context": ["The workspace root is '.'."],
    }

    tool_result = {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": "Found 0 matches for empty folders under .",
        "action": "find_paths",
        "requested_root": ".",
        "requested_query": "empty folders",
        "requested_mode": "basename",
        "match_count": 0,
        "matches": [],
    }

    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        (workspace / "notes.txt").write_text("keep me", encoding="utf-8")
        checker = FileWorkChecker(_LLM(), Queue(), _Brain(workspace))
        current = checker.verify_current_file_stage_state(stage, tool_result)

    stage_requires_verification = FileStagePolicy.stage_requires_file_verification(stage)
    current_verdict = str((current or {}).get("verdict", "")).upper()
    current_reason = str((current or {}).get("reason", "")).strip()
    success = (
        stage_requires_verification
        and current_verdict == "VERIFIED"
        and "absence-based success condition is satisfied" in current_reason.lower()
    )
    return FileDeleteAbsenceConfirmationReport(
        success=bool(success),
        stage_requires_verification=bool(stage_requires_verification),
        current_verdict=current_verdict,
        current_reason=current_reason,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify current-state FILE_WORK checking can certify absence-confirmed delete stages after a zero-match find_paths lookup."
    )


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
