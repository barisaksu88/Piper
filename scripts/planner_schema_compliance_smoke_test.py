"""planner_schema_compliance_smoke_test.py

Verifies the R-5 Planner Schema Compliance implementation:

  1. note_constraint_violation   — appends a correctly-formatted alert line to
                                   the alerts file (the fallthrough logging path)
  2. stage_requires_file_verification — discriminates mutating FILE_WORK stages
                                        (gate triggers) from exempt stage types
  3. derive_constraints          — explicit passthrough; MOVED/DELETED derivation
                                   from tool results; no CREATED auto-derivation;
                                   empty list when no evidence is available
  4. manager_prompt_requires_constraints — the constraints field is explicitly
                                           labelled required in manager.txt
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.services.file_work import FileWorkEngine  # noqa: E402
from core.services.stats_collector import StatsCollector  # noqa: E402
from core.file_stage_policy import FileStagePolicy  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fw_stage(goal: str = "Edit source.py", success: str = "source.py is updated") -> dict:
    """A mutating FILE_WORK stage that should require verification."""
    return {
        "stage_type": "FILE_WORK",
        "stage_goal": goal,
        "success_condition": success,
        "allowed_tools": ["FILE_OP"],
        "context": [],
    }


def _non_mutating_stage() -> dict:
    """A lookup/inspection FILE_WORK stage — no verification gate."""
    return {
        "stage_type": "FILE_WORK",
        "stage_goal": "Read and inspect the file contents of readme.txt",
        "success_condition": "The exact contents of readme.txt are reported",
        "allowed_tools": ["FILE_OP"],
        "context": [],
    }


def _script_launch_stage() -> dict:
    return {
        "stage_type": "FILE_WORK",
        "stage_goal": "Launch game.py to verify it runs without error",
        "success_condition": "The script starts without crashing",
        "allowed_tools": ["RUN_CODE"],
        "context": [],
    }


def _chat_stage() -> dict:
    return {
        "stage_type": "CHAT",
        "stage_goal": "Respond to the user question",
        "success_condition": "User question is answered",
        "allowed_tools": [],
        "context": [],
    }


# ---------------------------------------------------------------------------
# 1. note_constraint_violation
# ---------------------------------------------------------------------------

def _test_note_constraint_violation() -> tuple[bool, bool, bool]:
    with tempfile.TemporaryDirectory() as tmpdir:
        alerts_path = Path(tmpdir) / "alerts.txt"
        stats_path = Path(tmpdir) / "stats.jsonl"
        collector = StatsCollector(stats_path=stats_path, alerts_path=alerts_path)

        # Single call — should create the file and write one line.
        collector.note_constraint_violation(stage_goal="Edit main.py to fix the bug", attempt=2)

        ok_file_created = alerts_path.exists()
        if not ok_file_created:
            return False, False, False

        lines = [line.rstrip() for line in alerts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        ok_one_line = len(lines) == 1

        line = lines[0] if lines else ""
        ok_format = (
            "constraint_violation" in line
            and "attempt=2" in line
            and "Edit main.py to fix the bug" in line
        )

        # Second call — should append a second line, not overwrite.
        collector.note_constraint_violation(stage_goal="Create output.txt", attempt=2)
        lines_after = [line.rstrip() for line in alerts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        ok_appended = len(lines_after) == 2 and "Create output.txt" in lines_after[-1]

    return ok_file_created and ok_one_line, ok_format, ok_appended


# ---------------------------------------------------------------------------
# 2. stage_requires_file_verification
# ---------------------------------------------------------------------------

def _test_stage_requires_file_verification() -> tuple[bool, bool, bool, bool]:
    mutating = _fw_stage("Rewrite and edit the source code in main.py")
    ok_mutating = FileStagePolicy.stage_requires_file_verification(mutating)

    non_mutating = _non_mutating_stage()
    ok_non_mutating = not FileStagePolicy.stage_requires_file_verification(non_mutating)

    script = _script_launch_stage()
    ok_script = not FileStagePolicy.stage_requires_file_verification(script)

    chat = _chat_stage()
    ok_chat = not FileStagePolicy.stage_requires_file_verification(chat)

    return ok_mutating, ok_non_mutating, ok_script, ok_chat


# ---------------------------------------------------------------------------
# 3. derive_constraints
# ---------------------------------------------------------------------------

def _test_derive_constraints() -> tuple[bool, bool, bool, bool, bool]:
    stage = _fw_stage()

    # 3a. Explicit constraints on the stage card are returned as-is.
    explicit_stage = {
        **stage,
        "constraints": [
            {"type": "CREATED", "scope": "FILE", "path": "output/result.txt"},
            {"type": "MODIFIED", "scope": "FILE", "path": "src/main.py"},
        ],
    }
    explicit = FileWorkEngine.derive_constraints(explicit_stage)
    ok_explicit = len(explicit) == 2 and explicit[0]["type"] == "CREATED" and explicit[1]["type"] == "MODIFIED"

    # 3b. MOVED derived from a single-move tool result.
    moved_result = {
        "requested_moves": [{"src": "old/notes.txt", "dst": "archive/notes.txt"}],
    }
    moved = FileWorkEngine.derive_constraints(stage, moved_result)
    ok_moved = (
        len(moved) == 1
        and moved[0]["type"] == "MOVED"
        and moved[0]["from_path"] == "old/notes.txt"
        and moved[0]["to_path"] == "archive/notes.txt"
    )

    # 3c. DELETED derived from a single-file deletion.
    deleted_result = {"deleted_files": ["tmp/cache.txt"]}
    deleted = FileWorkEngine.derive_constraints(stage, deleted_result)
    ok_deleted = len(deleted) == 1 and deleted[0]["type"] == "DELETED" and deleted[0]["path"] == "tmp/cache.txt"

    # 3d. CREATED is NOT auto-derived from write_text results — must be explicit.
    write_result = {"action": "write_text", "files": {"src/output.py": "x = 1\n"}}
    created = FileWorkEngine.derive_constraints(stage, write_result)
    ok_no_created = all(c.get("type") != "CREATED" for c in created)

    # 3e. Bulk moves → empty list (no derivation for ambiguous bulk ops).
    bulk_result = {
        "requested_moves": [
            {"src": "a.txt", "dst": "archive/a.txt"},
            {"src": "b.txt", "dst": "archive/b.txt"},
        ]
    }
    bulk = FileWorkEngine.derive_constraints(stage, bulk_result)
    ok_bulk_empty = bulk == []

    return ok_explicit, ok_moved, ok_deleted, ok_no_created, ok_bulk_empty


# ---------------------------------------------------------------------------
# 4. manager_prompt_requires_constraints
# ---------------------------------------------------------------------------

def _test_manager_prompt_requires_constraints() -> bool:
    prompt_path = ROOT_DIR / "data" / "prompts" / "manager.txt"
    if not prompt_path.exists():
        return False
    text = prompt_path.read_text(encoding="utf-8")
    return (
        "constraints" in text
        and "required" in text
        and "FILE_WORK" in text
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SchemaSmokeReport:
    # 1. note_constraint_violation
    violation_file_created: bool
    violation_format_ok: bool
    violation_appended: bool

    # 2. stage_requires_file_verification
    requires_verification_mutating: bool
    no_verification_non_mutating: bool
    no_verification_script_launch: bool
    no_verification_chat: bool

    # 3. derive_constraints
    derive_explicit_passthrough: bool
    derive_moved_single: bool
    derive_deleted_single: bool
    no_derive_created_auto: bool
    no_derive_bulk_moves: bool

    # 4. manager prompt
    manager_prompt_requires_constraints: bool

    success: bool


def run_smoke() -> SchemaSmokeReport:
    v1, v2, v3 = _test_note_constraint_violation()
    r1, r2, r3, r4 = _test_stage_requires_file_verification()
    d1, d2, d3, d4, d5 = _test_derive_constraints()
    m1 = _test_manager_prompt_requires_constraints()

    success = all([v1, v2, v3, r1, r2, r3, r4, d1, d2, d3, d4, d5, m1])

    return SchemaSmokeReport(
        violation_file_created=v1,
        violation_format_ok=v2,
        violation_appended=v3,
        requires_verification_mutating=r1,
        no_verification_non_mutating=r2,
        no_verification_script_launch=r3,
        no_verification_chat=r4,
        derive_explicit_passthrough=d1,
        derive_moved_single=d2,
        derive_deleted_single=d3,
        no_derive_created_auto=d4,
        no_derive_bulk_moves=d5,
        manager_prompt_requires_constraints=m1,
        success=success,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke test R-5 planner schema compliance: constraint violation logging, "
                    "verification gate discrimination, and derive_constraints derivation paths."
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"  violation_file_created:          {report.violation_file_created}")
        print(f"  violation_format_ok:             {report.violation_format_ok}")
        print(f"  violation_appended:              {report.violation_appended}")
        print(f"  requires_verification_mutating:  {report.requires_verification_mutating}")
        print(f"  no_verification_non_mutating:    {report.no_verification_non_mutating}")
        print(f"  no_verification_script_launch:   {report.no_verification_script_launch}")
        print(f"  no_verification_chat:            {report.no_verification_chat}")
        print(f"  derive_explicit_passthrough:     {report.derive_explicit_passthrough}")
        print(f"  derive_moved_single:             {report.derive_moved_single}")
        print(f"  derive_deleted_single:           {report.derive_deleted_single}")
        print(f"  no_derive_created_auto:          {report.no_derive_created_auto}")
        print(f"  no_derive_bulk_moves:            {report.no_derive_bulk_moves}")
        print(f"  manager_prompt_requires_constraints: {report.manager_prompt_requires_constraints}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
