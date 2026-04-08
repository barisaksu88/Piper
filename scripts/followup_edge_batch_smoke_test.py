from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import file_append_constraints_smoke_test
import file_append_readback_undo_smoke_test
import file_event_close_then_delete_smoke_test
import file_event_mutex_smoke_test
import file_event_override_followup_smoke_test
import file_rename_move_correction_smoke_test
import file_rename_then_move_smoke_test
import file_task_collision_clarification_smoke_test
import file_task_collision_mutex_smoke_test
import file_work_state_isolation_smoke_test
import lookup_source_web_followup_smoke_test
import lookup_source_workspace_then_web_flip_smoke_test


@dataclass(frozen=True)
class BatchCaseReport:
    name: str
    success: bool
    data_dir: str
    kept_data_dir: str | None


@dataclass(frozen=True)
class FollowupEdgeBatchSmokeReport:
    success: bool
    cases: list[BatchCaseReport]


def _configure_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run_smoke(*, timeout: float, keep_data_copy: bool) -> FollowupEdgeBatchSmokeReport:
    case_modules = [
        ("file_event_mutex", file_event_mutex_smoke_test),
        ("file_event_override_followup", file_event_override_followup_smoke_test),
        ("file_event_close_then_delete", file_event_close_then_delete_smoke_test),
        ("file_task_collision_mutex", file_task_collision_mutex_smoke_test),
        ("file_task_collision_clarification", file_task_collision_clarification_smoke_test),
        ("file_append_constraints", file_append_constraints_smoke_test),
        ("file_append_readback_undo", file_append_readback_undo_smoke_test),
        ("file_rename_then_move", file_rename_then_move_smoke_test),
        ("file_rename_move_correction", file_rename_move_correction_smoke_test),
        ("lookup_source_web_followup", lookup_source_web_followup_smoke_test),
        ("lookup_source_workspace_then_web_flip", lookup_source_workspace_then_web_flip_smoke_test),
        ("file_work_state_isolation", file_work_state_isolation_smoke_test),
    ]

    cases: list[BatchCaseReport] = []
    overall_success = True
    for name, module in case_modules:
        report = module.run_smoke(timeout=timeout, keep_data_copy=keep_data_copy)
        success = bool(getattr(report, "success", False))
        overall_success = overall_success and success
        cases.append(
            BatchCaseReport(
                name=name,
                success=success,
                data_dir=str(getattr(report, "data_dir", "")),
                kept_data_dir=str(getattr(report, "kept_data_dir", "")) or None,
            )
        )
    return FollowupEdgeBatchSmokeReport(success=bool(overall_success), cases=cases)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the focused follow-up and correction edge-case harness batch sequentially."
    )
    parser.add_argument("--timeout", type=float, default=240.0, help="Per-case timeout in seconds.")
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
        print(f"SUCCESS: {report.success}")
        for case in report.cases:
            print(f"{case.name}: success={case.success}")
            print(f"  data_dir={case.data_dir}")
            if case.kept_data_dir:
                print(f"  kept_data_dir={case.kept_data_dir}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
