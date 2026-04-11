from __future__ import annotations

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
from core.file_checker_rules import LocalFileOpRuleChecker  # noqa: E402
from tools.workspace_extension_ops import build_extension_inventory  # noqa: E402


@dataclass(frozen=True)
class ExtensionReorgDirectoryPrepPartialReport:
    success: bool
    ensure_dirs_verdict: str
    ensure_dirs_reason: str
    current_verdict: str
    current_reason: str


class _Brain:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def _build_extension_inventory(
        self,
        root_path: Path,
        workspace_root: Path,
        *,
        extensions: set[str] | None = None,
    ) -> dict[str, object]:
        return build_extension_inventory(root_path, workspace_root, extensions=extensions)


class _LLM:
    pass


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_smoke() -> ExtensionReorgDirectoryPrepPartialReport:
    stage = {
        "stage_goal": "Consolidate files under './test' so each extension lives in one chosen destination folder without creating duplicates.",
        "stage_type": "FILE_WORK",
        "success_condition": "For every relevant extension under './test', files are consolidated into a single destination folder and duplicate identical files are not kept twice.",
        "allowed_tools": ["FILE_OP"],
        "active_targets": ["test"],
        "context": [
            "The workspace root is '.'.",
            "The requested reorganization root is './test'.",
            "Only reorganize files under './test'. Do not sweep the whole workspace root.",
        ],
    }

    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        _write_text(workspace / "test" / "file1.txt", "alpha\n")
        _write_text(workspace / "test" / "security.log", "warn\n")
        _write_text(workspace / "test" / "important_notes", "keep\n")
        (workspace / "test" / "text").mkdir(parents=True, exist_ok=True)
        (workspace / "test" / "logs").mkdir(parents=True, exist_ok=True)
        (workspace / "test" / "files").mkdir(parents=True, exist_ok=True)

        ensure_dirs_result = {
            "tool": "FILE_OP",
            "status": "EXECUTED",
            "summary": "Prepared 3 directories.",
            "action": "ensure_dirs",
            "requested_paths": ["test/text", "test/logs", "test/files"],
        }

        checker = LocalFileOpRuleChecker(workspace, stage)
        ensure_dirs_check = checker.evaluate(ensure_dirs_result) or {}

        current_check = FileWorkChecker(_LLM(), Queue(), _Brain(workspace)).verify_current_file_stage_state(
            stage,
            ensure_dirs_result,
        ) or {}

    ensure_dirs_verdict = str(ensure_dirs_check.get("verdict", "")).upper()
    ensure_dirs_reason = str(ensure_dirs_check.get("reason", "")).strip()
    current_verdict = str(current_check.get("verdict", "")).upper()
    current_reason = str(current_check.get("reason", "")).strip()
    success = (
        ensure_dirs_verdict == "PARTIAL"
        and "still requires moving or updating files" in ensure_dirs_reason.lower()
        and current_verdict == "FAILED"
        and "still outside their destination folders" in current_reason.lower()
        and "test/file1.txt" in current_reason
    )
    return ExtensionReorgDirectoryPrepPartialReport(
        success=bool(success),
        ensure_dirs_verdict=ensure_dirs_verdict,
        ensure_dirs_reason=ensure_dirs_reason,
        current_verdict=current_verdict,
        current_reason=current_reason,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
