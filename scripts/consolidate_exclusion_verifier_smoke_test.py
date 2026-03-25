"""consolidate_exclusion_verifier_smoke_test.py

Verifies that LocalFileOpRuleChecker._check_consolidate_by_extension returns FAILED
when a stage's success_condition mentions an exclusion (e.g. "except the FCOM") but
the tool result shows that excluded file was moved anyway.

Also verifies that a compliant result (excluded file NOT in created_files) still
passes as VERIFIED.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.file_checker_rules import LocalFileOpRuleChecker  # noqa: E402


@dataclass(frozen=True)
class ConsolidateExclusionReport:
    # Case 1: FCOM moved, stage says "except FCOM" → should be FAILED
    violation_verdict: str
    violation_reason_contains_pattern: bool
    violation_pass: bool

    # Case 2: FCOM NOT in created_files, no exclusion violation → should be VERIFIED
    compliant_verdict: str
    compliant_pass: bool

    # Case 3: stage has no exclusion clause at all → should be VERIFIED (normal path)
    no_exclusion_verdict: str
    no_exclusion_pass: bool

    # Case 4: excluded file (keep_me.txt) stays at root, excluded_prefixes set in tool_result
    # → should be VERIFIED (regression: keep_me.txt was incorrectly flagged as off-target)
    excluded_prefix_stays_verdict: str
    excluded_prefix_stays_pass: bool

    # Case 5: excluded file stays at root, excluded_names set in tool_result
    # → should be VERIFIED
    excluded_name_stays_verdict: str
    excluded_name_stays_pass: bool

    # Case 6: excluded file stays at root, no tool_result exclusion fields but stage text
    # mentions "except keep" → stage-text fallback should still return VERIFIED
    stage_text_fallback_verdict: str
    stage_text_fallback_pass: bool

    success: bool


def _make_checker(workspace: Path, goal: str, success_condition: str) -> LocalFileOpRuleChecker:
    stage = {
        "stage_goal": goal,
        "stage_type": "FILE_WORK",
        "success_condition": success_condition,
        "context": ["User wants to organise the workspace."],
        "allowed_tools": ["FILE_OP"],
    }
    return LocalFileOpRuleChecker(workspace, stage)


def _base_tool_result(created: list[str]) -> dict:
    return {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": "Consolidated files.",
        "action": "consolidate_by_extension",
        "requested_root": ".",
        "destinations": {
            ".pdf": "pdf",
            ".txt": "text_files",
            ".py": "python_scripts",
        },
        "created_files": created,
        "deleted_files": [],
        "requested_moves": [],
    }


def run_smoke() -> ConsolidateExclusionReport:
    with TemporaryDirectory() as tmp_dir:
        ws = Path(tmp_dir)

        # Build minimal workspace that satisfies the "compliant" destinations check
        (ws / "pdf").mkdir()
        (ws / "text_files").mkdir()
        (ws / "python_scripts").mkdir()
        (ws / "pdf" / "other_doc.pdf").write_bytes(b"pdf")
        (ws / "text_files" / "notes.txt").write_text("hi", encoding="utf-8")
        (ws / "python_scripts" / "script.py").write_text("pass\n", encoding="utf-8")

        # --- Case 1: Exclusion violated (FCOM moved) ---
        checker_violation = _make_checker(
            ws,
            goal="Put all files in relevant folders except the FCOM",
            success_condition="All files are organised into folders, except the FCOM file which must remain in place",
        )
        result_violation = checker_violation._check_consolidate_by_extension(
            _base_tool_result(
                created=[
                    "pdf/a320 - fcom - 03 dec 2025.pdf",  # FCOM was moved — violation
                    "text_files/notes.txt",
                    "python_scripts/script.py",
                ]
            )
        )
        v_verdict = str(result_violation.get("verdict", "")).upper()
        v_reason = str(result_violation.get("reason", "")).lower()
        v_pass = v_verdict == "FAILED" and "fcom" in v_reason

        # --- Case 2: Exclusion respected (FCOM NOT in created_files) ---
        checker_compliant = _make_checker(
            ws,
            goal="Put all files in relevant folders except the FCOM",
            success_condition="All files except FCOM files are organised in their relevant folders",
        )
        result_compliant = checker_compliant._check_consolidate_by_extension(
            _base_tool_result(
                created=[
                    "text_files/notes.txt",  # FCOM not here — fine
                    "python_scripts/script.py",
                ]
            )
        )
        c_verdict = str(result_compliant.get("verdict", "")).upper()
        c_pass = c_verdict == "VERIFIED"

        # --- Case 3: No exclusion clause in stage at all → normal VERIFIED path ---
        checker_no_excl = _make_checker(
            ws,
            goal="Consolidate all files by extension",
            success_condition="All files are in their destination folders",
        )
        result_no_excl = checker_no_excl._check_consolidate_by_extension(
            _base_tool_result(
                created=[
                    "pdf/other_doc.pdf",
                    "text_files/notes.txt",
                    "python_scripts/script.py",
                ]
            )
        )
        n_verdict = str(result_no_excl.get("verdict", "")).upper()
        n_pass = n_verdict == "VERIFIED"

    # Cases 4-6 use a separate workspace that contains keep_me.txt at the root
    # (intentionally excluded from consolidation).
    with TemporaryDirectory() as keep_dir:
        ws_keep = Path(keep_dir)
        (ws_keep / "pdf").mkdir()
        (ws_keep / "text_files").mkdir()
        (ws_keep / "python_scripts").mkdir()
        (ws_keep / "pdf" / "other_doc.pdf").write_bytes(b"pdf")
        (ws_keep / "text_files" / "notes.txt").write_text("hi", encoding="utf-8")
        (ws_keep / "python_scripts" / "script.py").write_text("pass\n", encoding="utf-8")
        # This file is intentionally kept at the root (excluded from consolidation)
        (ws_keep / "keep_me.txt").write_text("keep me", encoding="utf-8")

        # --- Case 4: keep_me.txt stays at root, excluded_prefixes: ["keep"] in tool_result ---
        # Regression test: the rule checker must NOT flag keep_me.txt as off-target when
        # excluded_prefixes covers it.
        checker_excl_prefix = _make_checker(
            ws_keep,
            goal="Consolidate all files by extension",
            success_condition="All files are in their destination folders",
        )
        tr_excl_prefix = {
            **_base_tool_result(created=["text_files/notes.txt", "python_scripts/script.py", "pdf/other_doc.pdf"]),
            "excluded_names": [],
            "excluded_prefixes": ["keep"],
        }
        result_excl_prefix = checker_excl_prefix._check_consolidate_by_extension(tr_excl_prefix)
        ep_verdict = str(result_excl_prefix.get("verdict", "")).upper()
        ep_pass = ep_verdict == "VERIFIED"

        # --- Case 5: keep_me.txt stays at root, excluded_names: ["keep_me.txt"] in tool_result ---
        checker_excl_name = _make_checker(
            ws_keep,
            goal="Consolidate all files by extension",
            success_condition="All files are in their destination folders",
        )
        tr_excl_name = {
            **_base_tool_result(created=["text_files/notes.txt", "python_scripts/script.py", "pdf/other_doc.pdf"]),
            "excluded_names": ["keep_me.txt"],
            "excluded_prefixes": [],
        }
        result_excl_name = checker_excl_name._check_consolidate_by_extension(tr_excl_name)
        en_verdict = str(result_excl_name.get("verdict", "")).upper()
        en_pass = en_verdict == "VERIFIED"

        # --- Case 6: keep_me.txt stays at root, NO tool_result exclusion fields, but stage
        # text says "except keep files" → stage-text fallback should still give VERIFIED ---
        checker_stage_fallback = _make_checker(
            ws_keep,
            goal="Consolidate all files by extension except keep files",
            success_condition="All files are in their destination folders except keep files",
        )
        tr_no_excl_fields = _base_tool_result(
            created=["text_files/notes.txt", "python_scripts/script.py", "pdf/other_doc.pdf"]
        )
        # Ensure no exclusion fields are present (simulates a synthetic STATE_CHECK result)
        tr_no_excl_fields.pop("excluded_names", None)
        tr_no_excl_fields.pop("excluded_prefixes", None)
        result_stage_fallback = checker_stage_fallback._check_consolidate_by_extension(tr_no_excl_fields)
        sf_verdict = str(result_stage_fallback.get("verdict", "")).upper()
        sf_pass = sf_verdict == "VERIFIED"

    success = v_pass and c_pass and n_pass and ep_pass and en_pass and sf_pass
    return ConsolidateExclusionReport(
        violation_verdict=v_verdict,
        violation_reason_contains_pattern=("fcom" in v_reason),
        violation_pass=v_pass,
        compliant_verdict=c_verdict,
        compliant_pass=c_pass,
        no_exclusion_verdict=n_verdict,
        no_exclusion_pass=n_pass,
        excluded_prefix_stays_verdict=ep_verdict,
        excluded_prefix_stays_pass=ep_pass,
        excluded_name_stays_verdict=en_verdict,
        excluded_name_stays_pass=en_pass,
        stage_text_fallback_verdict=sf_verdict,
        stage_text_fallback_pass=sf_pass,
        success=success,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify that exclusion violations in consolidate_by_extension are caught by the file checker."
    )


def main() -> int:
    _ = build_parser().parse_args()
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
