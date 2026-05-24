from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

from _bootstrap import ROOT_DIR

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CFG
from core.services.stats_collector import StatsCollector, TurnStatsState
from core.executor import StageExecutor


@dataclass(frozen=True)
class ExecutorBudgetSmokeReport:
    success: bool
    timeout_break_ok: bool
    timeout_metrics_ok: bool
    timeout_stats_ok: bool
    timeout_outcome_ok: bool
    timeout_after_action_ok: bool
    action_break_ok: bool
    action_metrics_ok: bool
    action_stats_ok: bool
    timeout_tool_calls: int
    timeout_after_action_tool_calls: int
    action_tool_calls: int


class _FakeUI:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def put(self, event: tuple[str, object]) -> None:
        self.events.append(event)


class _FakeLLM:
    def __init__(self, response_json: str, *, delay_s: float = 0.0) -> None:
        self.response_json = response_json
        self.delay_s = max(0.0, float(delay_s or 0.0))
        self.call_count = 0

    def generate(self, messages, temperature=0.0, max_tokens=0, cancel_token=None):  # noqa: ANN001
        self.call_count += 1
        if self.delay_s > 0.0:
            time.sleep(self.delay_s)
        return self.response_json


class _FakeBrain:
    def __init__(self, workspace: Path, execute_result: dict[str, object] | None = None) -> None:
        self.workspace = workspace
        self.execute_result = dict(execute_result or {})
        self.tool_calls = 0

    def parse_and_execute(self, tool_tag: str, cancel_token=None):  # noqa: ANN001
        self.tool_calls += 1
        return SimpleNamespace(
            action_type="TOOL",
            tag="FILE_OP",
            payload=tool_tag,
            execute_result=dict(self.execute_result),
        )


def _stage_card(*, stage_type: str = "FILE_WORK", file_stage_kind: str = "INSPECTION") -> dict[str, object]:
    return {
        "stage_goal": "Inspect the workspace inventory for diagnostics.",
        "stage_type": stage_type,
        "success_condition": "Collect enough inspection evidence to proceed.",
        "allowed_tools": ["FILE_OP"],
        "file_stage_kind": file_stage_kind,
        "active_targets": [],
        "context": [],
    }


def _make_collector(tmp_root: Path) -> StatsCollector:
    return StatsCollector(tmp_root / "stats.jsonl", tmp_root / "alerts.log")


def _stage_stats_ok(metrics: dict[str, object], *, timeout_hit: bool, action_budget_hit: bool, action_count: int) -> bool:
    return (
        bool(metrics)
        and bool(metrics.get("timeout_hit")) is timeout_hit
        and bool(metrics.get("action_budget_hit")) is action_budget_hit
        and int(metrics.get("action_count") or 0) == action_count
        and int(metrics.get("step_count") or 0) >= 1
    )


def run_smoke() -> ExecutorBudgetSmokeReport:
    timeout_response = json.dumps(
        {
            "thought": "Still gathering context.",
            "tool": "",
            "is_complete": False,
            "proposal": "",
        }
    )
    action_response = json.dumps(
        {
            "thought": "Try one more workspace inspection.",
            "tool": '[FILE_OP] {"action":"list_tree","root":".","max_depth":1} [/FILE_OP]',
            "is_complete": False,
            "proposal": "",
        }
    )
    failed_file_op_result = {
        "tool": "FILE_OP",
        "status": "FAILED",
        "summary": "Synthetic FILE_OP failure for budget smoke coverage.",
        "action": "list_tree",
        "workspace_changed": False,
        "created_files": [],
        "updated_files": [],
        "deleted_files": [],
        "created_dirs": [],
        "deleted_dirs": [],
        "evidence_files": [],
        "file_snippets": {},
    }
    successful_mutation_result = {
        "tool": "FILE_OP",
        "status": "EXECUTED",
        "summary": "Moved moved/demo.txt.",
        "action": "move_path",
        "workspace_changed": True,
        "created_files": [],
        "updated_files": ["moved/demo.txt"],
        "deleted_files": [],
        "created_dirs": [],
        "deleted_dirs": [],
        "evidence_files": ["moved/demo.txt"],
        "file_snippets": {},
    }

    old_runtime = float(getattr(CFG, "EXECUTOR_MAX_STAGE_RUNTIME_S", 120.0) or 120.0)
    old_actions = int(getattr(CFG, "EXECUTOR_MAX_ACTIONS_PER_STAGE", 15) or 15)

    try:
        with tempfile.TemporaryDirectory(prefix="piper-executor-budget-") as tmp:
            tmp_root = Path(tmp)

            timeout_ui = _FakeUI()
            timeout_llm = _FakeLLM(timeout_response, delay_s=0.06)
            timeout_brain = _FakeBrain(tmp_root / "workspace-timeout")
            timeout_executor = StageExecutor(
                timeout_llm,
                timeout_brain,
                img_gen=None,
                boot_mgr=None,
                ui_queue=timeout_ui,
            )
            object.__setattr__(CFG, "EXECUTOR_MAX_STAGE_RUNTIME_S", 0.05)
            object.__setattr__(CFG, "EXECUTOR_MAX_ACTIONS_PER_STAGE", old_actions)
            success_timeout, timeout_scratchpad = timeout_executor.run(_stage_card(), 1, 1)
            timeout_metrics = dict(timeout_executor._last_stage_metrics or {})
            timeout_break_ok = (
                (not success_timeout)
                and any("=== STAGE TIMEOUT ===" in str(entry) for entry in timeout_scratchpad)
                and timeout_brain.tool_calls == 0
            )
            timeout_metrics_ok = _stage_stats_ok(
                timeout_metrics,
                timeout_hit=True,
                action_budget_hit=False,
                action_count=0,
            )

            timeout_state = TurnStatsState()
            timeout_collector = _make_collector(tmp_root)
            timeout_collector.add_stage(
                timeout_state,
                index=1,
                stage=_stage_card(),
                planner_ms=float(timeout_metrics.get("planner_ms") or 0.0),
                executor_ms=float(timeout_metrics.get("executor_ms") or 0.0),
                total_ms=float(timeout_metrics.get("stage_total_ms") or 0.0),
                verification="FAILED",
                status="TIMEOUT",
                effective_success=False,
                step_count=int(timeout_metrics.get("step_count") or 0),
                action_count=int(timeout_metrics.get("action_count") or 0),
                timeout_hit=bool(timeout_metrics.get("timeout_hit")),
                action_budget_hit=bool(timeout_metrics.get("action_budget_hit")),
            )
            timeout_stage_stats = timeout_state.stages[-1] if timeout_state.stages else {}
            timeout_stats_ok = bool(timeout_stage_stats.get("timeout_hit")) and not bool(timeout_stage_stats.get("action_budget_hit"))
            timeout_collector.finalize_outcome(timeout_state)
            timeout_outcome_ok = str(timeout_state.outcome or "").strip().upper() == "TIMEOUT"

            timeout_after_action_ui = _FakeUI()
            timeout_after_action_llm = _FakeLLM(action_response, delay_s=0.06)
            timeout_after_action_brain = _FakeBrain(tmp_root / "workspace-timeout-action", successful_mutation_result)
            timeout_after_action_executor = StageExecutor(
                timeout_after_action_llm,
                timeout_after_action_brain,
                img_gen=None,
                boot_mgr=None,
                ui_queue=timeout_after_action_ui,
            )
            object.__setattr__(CFG, "EXECUTOR_MAX_STAGE_RUNTIME_S", 0.05)
            object.__setattr__(CFG, "EXECUTOR_MAX_ACTIONS_PER_STAGE", old_actions)
            success_timeout_after_action, timeout_after_action_scratchpad = timeout_after_action_executor.run(
                _stage_card(stage_type="SEARCH_WORK", file_stage_kind="UNKNOWN"),
                1,
                1,
            )
            timeout_after_action_ok = (
                (not success_timeout_after_action)
                and timeout_after_action_brain.tool_calls == 1
                and any("=== STAGE TIMEOUT ===" in str(entry) for entry in timeout_after_action_scratchpad)
                and any("Workspace mutations were already applied to: moved/demo.txt." in str(entry) for entry in timeout_after_action_scratchpad)
            )

            action_ui = _FakeUI()
            action_llm = _FakeLLM(action_response)
            action_brain = _FakeBrain(tmp_root / "workspace-actions", failed_file_op_result)
            action_executor = StageExecutor(
                action_llm,
                action_brain,
                img_gen=None,
                boot_mgr=None,
                ui_queue=action_ui,
            )
            object.__setattr__(CFG, "EXECUTOR_MAX_STAGE_RUNTIME_S", old_runtime)
            object.__setattr__(CFG, "EXECUTOR_MAX_ACTIONS_PER_STAGE", 2)
            success_action, action_scratchpad = action_executor.run(_stage_card(), 1, 1)
            action_metrics = dict(action_executor._last_stage_metrics or {})
            action_break_ok = (
                (not success_action)
                and any("=== ACTION BUDGET EXHAUSTED ===" in str(entry) for entry in action_scratchpad)
                and action_brain.tool_calls == 2
            )
            action_metrics_ok = _stage_stats_ok(
                action_metrics,
                timeout_hit=False,
                action_budget_hit=True,
                action_count=2,
            )

            action_state = TurnStatsState()
            action_collector = _make_collector(tmp_root)
            action_collector.add_stage(
                action_state,
                index=1,
                stage=_stage_card(),
                planner_ms=float(action_metrics.get("planner_ms") or 0.0),
                executor_ms=float(action_metrics.get("executor_ms") or 0.0),
                total_ms=float(action_metrics.get("stage_total_ms") or 0.0),
                verification="FAILED",
                status="ACTION BUDGET EXHAUSTED",
                effective_success=False,
                step_count=int(action_metrics.get("step_count") or 0),
                action_count=int(action_metrics.get("action_count") or 0),
                timeout_hit=bool(action_metrics.get("timeout_hit")),
                action_budget_hit=bool(action_metrics.get("action_budget_hit")),
            )
            action_stage_stats = action_state.stages[-1] if action_state.stages else {}
            action_stats_ok = bool(action_stage_stats.get("action_budget_hit")) and not bool(action_stage_stats.get("timeout_hit"))

            success = all(
                [
                    timeout_break_ok,
                    timeout_metrics_ok,
                    timeout_stats_ok,
                    timeout_outcome_ok,
                    timeout_after_action_ok,
                    action_break_ok,
                    action_metrics_ok,
                    action_stats_ok,
                ]
            )
            return ExecutorBudgetSmokeReport(
                success=bool(success),
                timeout_break_ok=bool(timeout_break_ok),
                timeout_metrics_ok=bool(timeout_metrics_ok),
                timeout_stats_ok=bool(timeout_stats_ok),
                timeout_outcome_ok=bool(timeout_outcome_ok),
                timeout_after_action_ok=bool(timeout_after_action_ok),
                action_break_ok=bool(action_break_ok),
                action_metrics_ok=bool(action_metrics_ok),
                action_stats_ok=bool(action_stats_ok),
                timeout_tool_calls=int(timeout_brain.tool_calls),
                timeout_after_action_tool_calls=int(timeout_after_action_brain.tool_calls),
                action_tool_calls=int(action_brain.tool_calls),
            )
    finally:
        object.__setattr__(CFG, "EXECUTOR_MAX_STAGE_RUNTIME_S", old_runtime)
        object.__setattr__(CFG, "EXECUTOR_MAX_ACTIONS_PER_STAGE", old_actions)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test executor stage runtime and action budget guards.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the final report as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke()
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    else:
        print(f"SUCCESS: {report.success}")
        print(f"TIMEOUT_BREAK_OK: {report.timeout_break_ok}")
        print(f"TIMEOUT_METRICS_OK: {report.timeout_metrics_ok}")
        print(f"TIMEOUT_STATS_OK: {report.timeout_stats_ok}")
        print(f"TIMEOUT_OUTCOME_OK: {report.timeout_outcome_ok}")
        print(f"TIMEOUT_AFTER_ACTION_OK: {report.timeout_after_action_ok}")
        print(f"ACTION_BREAK_OK: {report.action_break_ok}")
        print(f"ACTION_METRICS_OK: {report.action_metrics_ok}")
        print(f"ACTION_STATS_OK: {report.action_stats_ok}")
        print(f"TIMEOUT_TOOL_CALLS: {report.timeout_tool_calls}")
        print(f"TIMEOUT_AFTER_ACTION_TOOL_CALLS: {report.timeout_after_action_tool_calls}")
        print(f"ACTION_TOOL_CALLS: {report.action_tool_calls}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
