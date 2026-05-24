"""Smoke coverage for planner tool-call normalization.

No LLM or server required.  This protects the executor boundary from local
models that emit structured tool objects instead of Piper's bracket-tag string.
"""
from __future__ import annotations

import json
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.json_utils import normalize_tool_invocation, parse_json_response  # noqa: E402
from core.executor import StageExecutor  # noqa: E402
from core.services.summary import SummaryEngine  # noqa: E402
from core.planner_boundary import PlannerBoundary  # noqa: E402
from core.prompting import ScratchpadFormatter  # noqa: E402
from tools.file_ops import parse_normalized_tool_tag_payload  # noqa: E402


class _NoLlm:
    def generate(self, *_args, **_kwargs):
        raise AssertionError("preflight should stop before planner LLM generation")


class _Ui:
    def __init__(self) -> None:
        self.events = []

    def put(self, event) -> None:
        self.events.append(event)


def test_structured_file_op_tool_dict() -> None:
    decision = parse_json_response(
        json.dumps(
            {
                "thought": "Inspect the target folder.",
                "tool": {
                    "name": "FILE_OP",
                    "arguments": {
                        "action": "list_tree",
                        "root": ".",
                        "max_depth": 2,
                    },
                },
                "is_complete": False,
                "proposal": "",
            }
        )
    )
    assert decision["tool"].startswith("[FILE_OP]")
    assert '"action": "list_tree"' in decision["tool"]
    assert '"root": "."' in decision["tool"]


def test_sibling_file_op_arguments() -> None:
    decision = parse_json_response(
        json.dumps(
            {
                "thought": "Find matching files.",
                "tool": "FILE_OP",
                "action": "find_paths",
                "root": ".",
                "query": "report",
                "mode": "basename",
                "is_complete": False,
                "proposal": "",
            }
        )
    )
    assert decision["tool"].startswith("[FILE_OP]")
    assert '"action": "find_paths"' in decision["tool"]
    assert '"query": "report"' in decision["tool"]


def test_raw_dict_response_is_safe() -> None:
    decision = parse_json_response(
        {
            "thought": "Inspect the target folder.",
            "tool": {
                "name": "FILE_OP",
                "arguments": {"action": "list_tree", "root": "."},
            },
            "is_complete": False,
        }
    )
    assert decision["tool"].startswith("[FILE_OP]")


def test_normalize_run_code_tool_dict() -> None:
    tag = normalize_tool_invocation({"name": "RUN_CODE", "arguments": {"code": "print('ok')"}})
    assert tag.startswith("[RUN_CODE]")
    assert "print('ok')" in tag


def test_file_op_root_is_scoped_to_declared_target_directory() -> None:
    stage = {
        "stage_goal": "Analyze file types, sizes, and naming patterns.",
        "stage_type": "FILE_WORK",
        "success_condition": "Analysis report with observations is ready.",
        "file_stage_kind": "INSPECTION",
        "context": ["Target directory: data/workspace/langgraph_approval_test"],
    }
    tag = StageExecutor._apply_declared_scope_to_file_op_tag(
        '[FILE_OP] {"action":"list_tree","root":".","max_depth":2} [/FILE_OP]',
        stage,
    )
    payload = parse_normalized_tool_tag_payload(tag, tag="FILE_OP")
    assert payload["root"] == "langgraph_approval_test"

    repo_prefixed_tag = StageExecutor._apply_declared_scope_to_file_op_tag(
        '[FILE_OP] {"action":"list_tree","root":"data/workspace/langgraph_approval_test","max_depth":2} [/FILE_OP]',
        stage,
    )
    repo_prefixed_payload = parse_normalized_tool_tag_payload(repo_prefixed_tag, tag="FILE_OP")
    assert repo_prefixed_payload["root"] == "langgraph_approval_test"


def test_missing_declared_scope_is_terminal_for_file_work() -> None:
    stage = {
        "stage_goal": "List all files and subdirectories within data/workspace/langgraph_approval_test.",
        "stage_type": "FILE_WORK",
        "success_condition": "Complete inventory of files and folders is generated.",
        "file_stage_kind": "INSPECTION",
        "context": ["Target directory: data/workspace/langgraph_approval_test"],
    }
    PlannerBoundary.validate_input(stage)
    tool_result = {
        "tool": "FILE_OP",
        "status": "FAILED",
        "summary": "FILE_OP target not found: langgraph_approval_test",
        "action": "list_tree",
        "requested_root": "langgraph_approval_test",
    }

    executor = object.__new__(StageExecutor)
    executor.brain = SimpleNamespace(workspace=Path("."))
    assert executor._is_terminal_missing_existing_file_target(stage, tool_result)

    entry = ScratchpadFormatter.format_step(
        1,
        "Inspect the declared directory.",
        '[FILE_OP] {"action":"list_tree","root":"langgraph_approval_test"} [/FILE_OP]',
        tool_result,
    )
    assert ScratchpadFormatter._has_terminal_missing_named_file_target_failure(
        stage=stage,
        stage_entries=[entry],
    )


def test_missing_declared_scope_preflights_before_planner_loop() -> None:
    stage = {
        "stage_goal": "List all files and subdirectories within data/workspace/langgraph_approval_test.",
        "stage_type": "FILE_WORK",
        "success_condition": "Complete inventory of files and folders is generated.",
        "file_stage_kind": "INSPECTION",
        "context": ["Target directory: data/workspace/langgraph_approval_test"],
    }
    PlannerBoundary.validate_input(stage)

    with TemporaryDirectory() as tmp:
        executor = object.__new__(StageExecutor)
        executor.brain = SimpleNamespace(workspace=Path(tmp))
        assert executor._preflight_missing_declared_scope_target(stage) == "langgraph_approval_test"

        (Path(tmp) / "langgraph_approval_test").mkdir()
        assert executor._preflight_missing_declared_scope_target(stage) == ""


def test_missing_declared_scope_run_stops_before_llm() -> None:
    stage = {
        "stage_goal": "List all files and subdirectories within data/workspace/langgraph_approval_test.",
        "stage_type": "FILE_WORK",
        "success_condition": "Complete inventory of files and folders is generated.",
        "file_stage_kind": "INSPECTION",
        "context": ["Target directory: data/workspace/langgraph_approval_test"],
    }

    with TemporaryDirectory() as tmp:
        ui = _Ui()
        executor = StageExecutor(
            _NoLlm(),
            SimpleNamespace(workspace=Path(tmp)),
            img_gen=None,
            boot_mgr=None,
            ui_queue=ui,
        )
        success, scratchpad = executor.run(stage, 1, 1)

    assert not success
    assert executor.terminal_missing_file_target == "langgraph_approval_test"
    assert executor._last_stage_metrics["action_count"] == 0
    assert any("preflight_scope_check" in str(entry) for entry in scratchpad)


def test_file_op_failure_detail_prefers_structured_summary() -> None:
    stage = {
        "stage_goal": "List all files and subdirectories within data/workspace/langgraph_approval_test.",
        "stage_type": "FILE_WORK",
        "success_condition": "Complete inventory of files and folders is generated.",
        "file_stage_kind": "INSPECTION",
        "context": ["Target directory: data/workspace/langgraph_approval_test"],
    }
    PlannerBoundary.validate_input(stage)
    entry = ScratchpadFormatter.format_step(
        1,
        "Declared scope is missing.",
        "[NO_TOOL_PREFLIGHT]",
        {
            "tool": "FILE_OP",
            "status": "FAILED",
            "summary": "FILE_OP target not found: langgraph_approval_test",
            "action": "preflight_scope_check",
            "requested_root": "langgraph_approval_test",
        },
    )

    assert SummaryEngine.extract_observation_detail(entry) == "FILE_OP target not found: langgraph_approval_test"
    pack = ScratchpadFormatter.build_outcome_pack(
        success=False,
        stage_type="FILE_WORK",
        last_observation=entry,
        stage_entries=[entry],
        stage=stage,
    )
    assert pack.detail == "FILE_OP target not found: langgraph_approval_test"
    assert not pack.allow_persona_reroute


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
