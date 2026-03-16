"""planner_boundary_smoke_test.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Isolated unit tests for ``core.planner_boundary``.

No server, no LLM, no I/O — purely exercises the contract enforcement logic.

Cases:
    validate_input
        1. Happy path — fully populated stage passes through unchanged
        2. Tool resolution — empty allowed_tools resolved from stage_type
        3. CHAT stage — tools always cleared regardless of input
        4. Active-target extraction — targets extracted from goal/condition text
        5. Evidence-required defaulting — falls back to success_condition
        6. Objective injection — written through from stage card
        7. Missing stage_goal — raises ValueError
        8. Missing success_condition — raises ValueError
        9. Fallback RUN_CODE — unknown domain with no tools gets safe default

    normalize_output
        10. Explicit is_complete=False, tool present — tool-use step
        11. Explicit is_complete=True, no proposal — clean completion
        12. Completion with question proposal — inferred clarification_requested
        13. Explicit clarification_requested flag wins regardless of proposal shape
        14. stop_recommended propagates correctly
        15. Missing/None fields default cleanly (no KeyError / AttributeError)
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.planner_boundary import PlannerBoundary, PlannerInput, PlannerOutput  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stage(
    *,
    stage_goal: str = "Edit grocery_list.txt to remove bread.",
    stage_type: str = "FILE_WORK",
    success_condition: str = "grocery_list.txt no longer contains bread.",
    allowed_tools=None,
    objective: str = "",
    active_targets=None,
    evidence_required: str = "",
) -> dict:
    s: dict = {"stage_goal": stage_goal, "stage_type": stage_type,
               "success_condition": success_condition}
    if allowed_tools is not None:
        s["allowed_tools"] = list(allowed_tools)
    if objective:
        s["objective"] = objective
    if active_targets is not None:
        s["active_targets"] = list(active_targets)
    if evidence_required:
        s["evidence_required"] = evidence_required
    return s


def _decision(
    *,
    thought: str = "",
    tool=None,
    is_complete: bool = False,
    proposal: str = "",
    clarification_requested: bool = False,
    stop_recommended: bool = False,
) -> dict:
    return {
        "thought": thought,
        "tool": tool,
        "is_complete": is_complete,
        "proposal": proposal,
        "clarification_requested": clarification_requested,
        "stop_recommended": stop_recommended,
    }


# ---------------------------------------------------------------------------
# validate_input tests
# ---------------------------------------------------------------------------

def test_happy_path_fully_populated() -> None:
    stage = _stage(
        allowed_tools=["FILE_OP", "RUN_CODE"],
        objective="Manage the grocery list.",
        active_targets=["grocery_list.txt"],
        evidence_required="File verified without bread.",
    )
    inp = PlannerBoundary.validate_input(stage, objective="Manage the grocery list.")
    assert isinstance(inp, PlannerInput)
    assert inp.stage_goal == "Edit grocery_list.txt to remove bread."
    assert inp.success_condition == "grocery_list.txt no longer contains bread."
    assert inp.objective == "Manage the grocery list."
    assert inp.allowed_tools == ["FILE_OP", "RUN_CODE"]
    assert inp.active_targets == ["grocery_list.txt"]
    assert inp.evidence_required == "File verified without bread."


def test_tool_resolution_from_stage_type() -> None:
    # Empty allowed_tools → resolved from stage_type via registry.
    stage = _stage(allowed_tools=[])
    inp = PlannerBoundary.validate_input(stage)
    assert len(inp.allowed_tools) > 0, "expected tools to be resolved from FILE_WORK domain"
    # The resolved list must also be written back into the stage dict.
    assert stage["allowed_tools"] == inp.allowed_tools


def test_chat_stage_clears_tools() -> None:
    stage = _stage(stage_type="CHAT", allowed_tools=["FILE_OP", "RUN_CODE"])
    inp = PlannerBoundary.validate_input(stage)
    assert inp.allowed_tools == [], f"CHAT stage must have empty tools, got {inp.allowed_tools}"
    assert stage["allowed_tools"] == []


def test_active_target_extraction_from_text() -> None:
    # No explicit active_targets → extracted heuristically from goal/condition.
    stage = _stage(
        stage_goal="Edit app.py to fix the import error.",
        success_condition="app.py imports correctly and tests pass.",
    )
    inp = PlannerBoundary.validate_input(stage)
    assert "app.py" in inp.active_targets, f"expected app.py in targets, got {inp.active_targets}"


def test_evidence_required_defaults_to_success_condition() -> None:
    stage = _stage()  # no evidence_required
    inp = PlannerBoundary.validate_input(stage)
    assert inp.evidence_required == stage["success_condition"]


def test_objective_from_stage_card() -> None:
    stage = _stage(objective="Parent route goal text.")
    inp = PlannerBoundary.validate_input(stage, objective="Parent route goal text.")
    assert inp.objective == "Parent route goal text."


def test_missing_stage_goal_raises() -> None:
    stage = _stage(stage_goal="")
    raised = False
    try:
        PlannerBoundary.validate_input(stage)
    except ValueError as exc:
        raised = True
        assert "stage_goal" in str(exc).lower()
    assert raised, "expected ValueError for empty stage_goal"


def test_missing_success_condition_raises() -> None:
    stage = _stage(success_condition="")
    raised = False
    try:
        PlannerBoundary.validate_input(stage)
    except ValueError as exc:
        raised = True
        assert "success_condition" in str(exc).lower()
    assert raised, "expected ValueError for empty success_condition"


def test_unknown_domain_gets_fallback_tool() -> None:
    # Unknown stage_type with no registry hits → safe default RUN_CODE.
    stage = _stage(stage_type="COMPLETELY_UNKNOWN_DOMAIN_XYZ", allowed_tools=[])
    inp = PlannerBoundary.validate_input(stage)
    assert inp.allowed_tools == ["RUN_CODE"], f"expected ['RUN_CODE'] fallback, got {inp.allowed_tools}"


# ---------------------------------------------------------------------------
# normalize_output tests
# ---------------------------------------------------------------------------

def test_tool_use_step() -> None:
    dec = _decision(thought="I will read the file.", tool="FILE_OP", is_complete=False)
    out = PlannerBoundary.normalize_output(dec)
    assert isinstance(out, PlannerOutput)
    assert out.tool == "FILE_OP"
    assert not out.is_complete
    assert not out.clarification_requested
    assert not out.stop_recommended


def test_clean_completion() -> None:
    dec = _decision(is_complete=True, proposal="File updated successfully.")
    out = PlannerBoundary.normalize_output(dec)
    assert out.is_complete
    assert not out.clarification_requested
    assert out.proposal == "File updated successfully."


def test_question_proposal_infers_clarification() -> None:
    # is_complete=True but proposal ends with '?' → inferred clarification.
    dec = _decision(is_complete=True, proposal="Which file should I edit?")
    out = PlannerBoundary.normalize_output(dec)
    assert out.clarification_requested, "question proposal should infer clarification_requested"


def test_explicit_clarification_flag_wins() -> None:
    # explicit clarification_requested=True, even with non-question proposal.
    dec = _decision(is_complete=True, proposal="Needs user input.", clarification_requested=True)
    out = PlannerBoundary.normalize_output(dec)
    assert out.clarification_requested


def test_stop_recommended_propagates() -> None:
    dec = _decision(stop_recommended=True, thought="Cannot proceed, missing file.")
    out = PlannerBoundary.normalize_output(dec)
    assert out.stop_recommended


def test_missing_fields_default_cleanly() -> None:
    # Minimal decision dict — no KeyError, no AttributeError.
    out = PlannerBoundary.normalize_output({})
    assert out.thought == ""
    assert out.tool is None
    assert not out.is_complete
    assert not out.clarification_requested
    assert not out.stop_recommended
    assert out.proposal == ""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_all() -> int:
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] {name}")
            passed += 1
        except Exception as exc:
            print(f"  [FAIL] {name}: {exc}")
            failed += 1
    print(f"\n{'ALL PASSED' if not failed else 'FAILURES DETECTED'} ({passed}/{passed + failed})")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(_run_all())
