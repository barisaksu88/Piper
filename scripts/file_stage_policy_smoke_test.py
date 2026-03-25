from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

import sys

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.file_stage_policy import FileStagePolicy  # noqa: E402


@dataclass(frozen=True)
class PolicySmokeReport:
    success: bool
    diagnosis_stage: dict[str, object]
    modify_stage: dict[str, object]
    interactive_stage: dict[str, object]
    typed_stage_consistency: dict[str, object]


def run_smoke() -> PolicySmokeReport:
    diagnosis_stage = {
        "stage_goal": "Inspect the game code to identify the root cause of the unresponsive movement buttons and broken star-catching logic.",
        "stage_type": "FILE_WORK",
        "success_condition": "A diagnosis is identified explaining why the input handlers for left/right and the star collision detection are failing.",
    }
    modify_stage = {
        "stage_goal": "Modify the game code to implement working left/right movement controls and functional star-catching mechanics.",
        "stage_type": "FILE_WORK",
        "success_condition": "The code changes are applied to fix the identified bugs.",
    }
    interactive_stage = {
        "stage_goal": "Re-run the game script to verify that the left/right buttons now move the player and the star-catching mechanic works correctly.",
        "stage_type": "FILE_WORK",
        "success_condition": "The game runs successfully with confirmed responsive controls and working star collection.",
    }
    read_result = {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "action": "read_text",
        "requested_path": "catch_the_stars.py",
        "path": "catch_the_stars.py",
        "files": {"catch_the_stars.py": "print('fixture')\n"},
    }

    diagnosis = {
        "inspection": FileStagePolicy.is_file_inspection_stage(diagnosis_stage),
        "non_mutating": FileStagePolicy.stage_is_non_mutating_file_stage(diagnosis_stage),
        "requires_verification": FileStagePolicy.stage_requires_file_verification(diagnosis_stage),
        "targeted_read": FileStagePolicy.stage_requires_targeted_read(diagnosis_stage),
        "requires_analysis_report": FileStagePolicy.stage_requires_analysis_report(diagnosis_stage),
        "inspection_satisfied_by_read": FileStagePolicy.non_mutating_file_stage_is_satisfied(
            diagnosis_stage,
            "FILE_OP",
            read_result,
        ),
    }
    modify = {
        "content_edit": FileStagePolicy.stage_is_content_edit_stage(modify_stage),
        "non_mutating": FileStagePolicy.stage_is_non_mutating_file_stage(modify_stage),
        "requires_verification": FileStagePolicy.stage_requires_file_verification(modify_stage),
    }
    interactive = {
        "script_launch": FileStagePolicy.stage_is_script_launch_stage(interactive_stage),
        "interactive_runtime_verification": FileStagePolicy.stage_is_interactive_runtime_verification(interactive_stage),
        "requires_verification": FileStagePolicy.stage_requires_file_verification(interactive_stage),
    }
    diagnosis_stage_typed = {**diagnosis_stage, "file_stage_kind": "INSPECTION"}
    modify_stage_typed = {**modify_stage, "file_stage_kind": "CONTENT_EDIT"}
    interactive_stage_typed = {**interactive_stage, "file_stage_kind": "SCRIPT_LAUNCH"}
    typed_stage_consistency = {
        "diagnosis_inspection": FileStagePolicy.is_file_inspection_stage(diagnosis_stage_typed),
        "diagnosis_non_mutating": FileStagePolicy.stage_is_non_mutating_file_stage(diagnosis_stage_typed),
        "diagnosis_requires_analysis_report": FileStagePolicy.stage_requires_analysis_report(diagnosis_stage_typed),
        "modify_content_edit": FileStagePolicy.stage_is_content_edit_stage(modify_stage_typed),
        "modify_requires_verification": FileStagePolicy.stage_requires_file_verification(modify_stage_typed),
        "interactive_script_launch": FileStagePolicy.stage_is_script_launch_stage(interactive_stage_typed),
        "interactive_requires_verification": FileStagePolicy.stage_requires_file_verification(interactive_stage_typed),
    }

    success = (
        diagnosis["inspection"]
        and diagnosis["non_mutating"]
        and not diagnosis["requires_verification"]
        and not diagnosis["targeted_read"]
        and diagnosis["requires_analysis_report"]
        and not diagnosis["inspection_satisfied_by_read"]
        and modify["content_edit"]
        and not modify["non_mutating"]
        and modify["requires_verification"]
        and interactive["script_launch"]
        and interactive["interactive_runtime_verification"]
        and not interactive["requires_verification"]
        and typed_stage_consistency["diagnosis_inspection"]
        and typed_stage_consistency["diagnosis_non_mutating"]
        and typed_stage_consistency["diagnosis_requires_analysis_report"]
        and typed_stage_consistency["modify_content_edit"]
        and typed_stage_consistency["modify_requires_verification"]
        and typed_stage_consistency["interactive_script_launch"]
        and not typed_stage_consistency["interactive_requires_verification"]
    )
    return PolicySmokeReport(
        success=bool(success),
        diagnosis_stage=diagnosis,
        modify_stage=modify,
        interactive_stage=interactive,
        typed_stage_consistency=typed_stage_consistency,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Run deterministic stage-policy checks for FILE_WORK diagnosis/edit/runtime verification flows.")


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
