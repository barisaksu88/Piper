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


@dataclass(frozen=True)
class FileCheckerTextContentInferenceReport:
    success: bool
    initial_verdict: str
    current_verdict: str
    current_reason: str


class _Brain:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace


class _LLM:
    pass


def run_smoke() -> FileCheckerTextContentInferenceReport:
    stage = {
        "stage_goal": "Create or overwrite the text file 'text_files/harness_alpha.txt' with the exact contents \"alpha beta gamma\".",
        "stage_type": "FILE_WORK",
        "success_condition": "The file 'text_files/harness_alpha.txt' exists and its exact contents are \"alpha beta gamma\".",
        "allowed_tools": ["FILE_OP"],
        "context": [
            "The workspace root is '.'.",
            "The target path is 'text_files/harness_alpha.txt'.",
        ],
    }

    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        target = workspace / "text_files" / "harness_alpha.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("alpha beta gamma", encoding="utf-8")
        checker = FileWorkChecker(_LLM(), Queue(), _Brain(workspace))
        tool_result = {
            "tool": "FILE_OP",
            "status": "EXECUTED",
            "summary": "Wrote text file: text_files/harness_alpha.txt",
            "action": "write_text",
            "requested_path": "text_files/harness_alpha.txt",
            "path": "text_files/harness_alpha.txt",
            "requested_content_sha1": "e65cdaae65857cc3e20aab132ee20f3538fc4f90",
            "created_files": ["text_files/harness_alpha.txt"],
            "evidence_files": ["text_files/harness_alpha.txt"],
            "file_snippets": {
                "text_files/harness_alpha.txt": {
                    "status": "text",
                    "truncated": False,
                    "full_char_count": 16,
                    "content": "alpha beta gamma",
                }
            },
        }
        initial = checker.run_file_checker(stage, tool_result)
        current = checker.verify_current_file_stage_state(stage, tool_result)

    initial_verdict = str((initial or {}).get("verdict", "")).upper()
    current_verdict = str((current or {}).get("verdict", "")).upper()
    current_reason = str((current or {}).get("reason", "")).strip()
    success = (
        initial_verdict == "VERIFIED"
        and current_verdict == "VERIFIED"
        and "matches the requested content exactly" in current_reason.lower()
    )
    return FileCheckerTextContentInferenceReport(
        success=bool(success),
        initial_verdict=initial_verdict,
        current_verdict=current_verdict,
        current_reason=current_reason,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify current-state text-content inference ignores incidental quoted context like workspace root '.'."
    )


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
