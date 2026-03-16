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
from tools.workspace_extension_ops import build_extension_inventory  # noqa: E402


@dataclass(frozen=True)
class ExtensionReorgCurrentStateVerifierReport:
    success: bool
    initial_verdict: str
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


def _write_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def run_smoke() -> ExtensionReorgCurrentStateVerifierReport:
    stage = {
        "stage_goal": "Consolidate files so each extension lives in one chosen destination folder without creating duplicates.",
        "stage_type": "FILE_WORK",
        "success_condition": "For every relevant extension, files are consolidated into a single destination folder and duplicate identical files are not kept twice.",
        "allowed_tools": ["FILE_OP"],
        "context": ["The workspace root is '.'."],
    }

    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        _write_json(workspace / ".json" / "reference_state.json", '{\n  "state": "ok"\n}\n')
        _write_json(workspace / ".json" / "misc_data.json", '{\n  "kind": "misc"\n}\n')
        _write_text(workspace / "text_files" / "trip_checklist.txt", "passport\ncharger\n")
        _write_text(workspace / "python_scripts" / "cleanup_plan.py", "print('cleanup')\n")
        _write_text(workspace / "images" / "reference_image.png", "png-bytes-placeholder")

        checker = FileWorkChecker(_LLM(), Queue(), _Brain(workspace))
        tool_result = {
            "tool": "FILE_OP",
            "status": "EXECUTED",
            "summary": "Consolidated 3 files across 4 extension groups and removed 0 duplicate files.",
            "action": "consolidate_by_extension",
            "requested_root": ".",
            "destinations": {
                ".json": ".json",
                ".txt": "text_files",
                ".py": "python_scripts",
                ".png": "images",
            },
            "requested_moves": [
                {"src": "misc/misc_data.json", "dst": ".json/misc_data.json"},
                {"src": "trip_checklist.txt", "dst": "text_files/trip_checklist.txt"},
                {"src": "cleanup_plan.py", "dst": "python_scripts/cleanup_plan.py"},
            ],
            "evidence_files": [
                ".json/misc_data.json",
                "text_files/trip_checklist.txt",
                "python_scripts/cleanup_plan.py",
                "images/reference_image.png",
            ],
        }
        initial = checker.run_file_checker(stage, tool_result)
        current = checker.verify_current_file_stage_state(stage, tool_result)

    initial_verdict = str((initial or {}).get("verdict", "")).upper()
    current_verdict = str((current or {}).get("verdict", "")).upper()
    current_reason = str((current or {}).get("reason", "")).strip()
    success = (
        initial_verdict == "VERIFIED"
        and current_verdict == "VERIFIED"
        and "consolidated into their chosen destination folders" in current_reason.lower()
    )
    return ExtensionReorgCurrentStateVerifierReport(
        success=bool(success),
        initial_verdict=initial_verdict,
        current_verdict=current_verdict,
        current_reason=current_reason,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify extension-based workspace reorg stages are not downgraded by generic current-state copy heuristics."
    )


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
