from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import core.orchestrator_phases as phases  # noqa: E402
from core.orchestrator_phases import phase_manager  # noqa: E402
from core.prompting import ScratchpadFormatter  # noqa: E402


class DummyUi:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def put(self, event) -> None:
        self.events.append(event)


class DummyStatsCollector:
    def start_phase(self, state, phase_name: str) -> None:
        del state, phase_name

    def end_phase(self, state, phase_name: str) -> float:
        del state, phase_name
        return 0.0

    def add_stage(self, state, **kwargs) -> None:
        del state, kwargs

    def note_route(self, state, **kwargs) -> None:
        del state, kwargs


class DummyPromptContext:
    operational_state_service = None

    def extract_latest_stage_proposal_answer(self, scratchpad: list[str]) -> str:
        del scratchpad
        return ""


class DummyOrchestrator:
    def __init__(self) -> None:
        self.llm = None
        self.brain = SimpleNamespace(workspace=ROOT_DIR / "data" / "workspace")
        self.img_gen = None
        self.boot = None
        self.ui = DummyUi()
        self.cancel_token = None
        self.prompt_context = DummyPromptContext()
        self.stats_collector = DummyStatsCollector()
        self.turn_stats = object()
        self.context_card = {
            "goal": "Clarify folder.",
            "stages": [
                {
                    "stage_goal": "Ask the user which folder to use.",
                    "stage_type": "CHAT",
                    "success_condition": "Await user input with the selected folder.",
                }
            ],
        }
        self.route_decision = {"decision": "TASK", "card": self.context_card}
        self.scratchpad: list[str] = []
        self.pending_file_target_confirmation = None
        self.pending_stage_pause = None
        self.last_stage_outcome = None
        self.last_verification = None
        self.next_stage = "MANAGER"

    def raise_if_cancelled(self) -> None:
        return

    def emit_runtime_signal(self, signal, *, scratchpad=None):  # noqa: ANN001
        del signal, scratchpad
        return None

    def _update_status(self, **kwargs) -> None:
        del kwargs

    def _log_dashboard(self, text: str) -> None:
        self.ui.put(("status_widget_dashboard_activity", text))


class DummyApprovalOrchestrator(DummyOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.context_card = {
            "goal": "Organize notes after approval.",
            "stages": [
                {
                    "stage_goal": "Present a notes organization plan for approval.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "Proposal is ready for user approval before executing.",
                },
                {
                    "stage_goal": "Apply the approved notes organization plan.",
                    "stage_type": "FILE_WORK",
                    "success_condition": "The approved organization changes are complete.",
                },
            ],
        }
        self.route_decision = {"decision": "TASK", "card": self.context_card}


class PausingExecutor:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs
        self.completed_change_operations: list[dict[str, object]] = []
        self.completed_rollback_manifests: list[str] = []
        self._last_verification = None
        self._last_stage_metrics = {"planner_ms": 1.0, "executor_ms": 2.0, "stage_total_ms": 3.0}
        self.pause_requested = False
        self.pause_mode = ""
        self.terminal_missing_file_target = ""
        self.scratchpad: list[str] = []

    def run(self, stage, stage_num: int, total_stages: int):  # noqa: ANN001
        del stage, total_stages
        self.pause_requested = True
        self.pause_mode = "user_input"
        self.scratchpad = [
            ScratchpadFormatter.format_step(
                stage_num,
                "Clarification ready",
                "[NO_TOOL_PROPOSAL]",
                "PROPOSAL: Which folder should I use?",
            )
        ]
        return True, list(self.scratchpad)


class ApprovalPausingExecutor(PausingExecutor):
    def run(self, stage, stage_num: int, total_stages: int):  # noqa: ANN001
        del stage, total_stages
        self.pause_requested = True
        self.pause_mode = "approval"
        self.scratchpad = [
            ScratchpadFormatter.format_step(
                stage_num,
                "Approval proposal ready",
                "[NO_TOOL_PROPOSAL]",
                "PROPOSAL: Move notes into topic folders?",
            )
        ]
        return True, list(self.scratchpad)


@dataclass(frozen=True)
class StagePauseSnapshotReport:
    success: bool
    user_input: dict[str, object]
    approval: dict[str, object]


def _run_phase_manager_with(executor_cls, orc):
    original_executor = phases.StageExecutor
    original_fire_hooks = phases.fire_hooks
    try:
        phases.StageExecutor = executor_cls
        phases.fire_hooks = lambda *args, **kwargs: None
        phase_manager(orc)
    finally:
        phases.StageExecutor = original_executor
        phases.fire_hooks = original_fire_hooks
    return orc


def main() -> int:
    orc = _run_phase_manager_with(PausingExecutor, DummyOrchestrator())

    pause = dict(orc.pending_stage_pause or {})
    user_input_success = bool(
        orc.next_stage == "PERSONA"
        and pause.get("kind") == "stage_pause"
        and pause.get("pause_type") == "user_input"
        and pause.get("question") == "Which folder should I use?"
        and pause.get("stage_num") == 1
        and str(getattr(orc.last_stage_outcome, "status", "")) == "PAUSED / AWAITING USER INPUT"
    )
    approval_orc = _run_phase_manager_with(ApprovalPausingExecutor, DummyApprovalOrchestrator())
    approval_pause = dict(approval_orc.pending_stage_pause or {})
    approved_stages = list(((approval_pause.get("approved_route_decision") or {}).get("card") or {}).get("stages") or [])
    approval_success = bool(
        approval_orc.next_stage == "PERSONA"
        and approval_pause.get("kind") == "stage_pause"
        and approval_pause.get("pause_type") == "approval"
        and approval_pause.get("question") == "Move notes into topic folders?"
        and approval_pause.get("approval_resume_mode") == "after_stage"
        and len(approved_stages) == 1
        and str((approved_stages[0] or {}).get("stage_goal") or "").startswith("Apply the approved")
        and str(getattr(approval_orc.last_stage_outcome, "status", "")) == "PAUSED / AWAITING USER APPROVAL"
    )
    report = StagePauseSnapshotReport(
        success=bool(user_input_success and approval_success),
        user_input={
            "success": user_input_success,
            "next_stage": str(orc.next_stage or ""),
            "pending_stage_pause": pause or None,
            "last_stage_status": str(getattr(orc.last_stage_outcome, "status", "")),
            "scratchpad_tail": [str(item) for item in orc.scratchpad[-3:]],
        },
        approval={
            "success": approval_success,
            "next_stage": str(approval_orc.next_stage or ""),
            "pending_stage_pause": approval_pause or None,
            "approved_stage_count": len(approved_stages),
            "last_stage_status": str(getattr(approval_orc.last_stage_outcome, "status", "")),
            "scratchpad_tail": [str(item) for item in approval_orc.scratchpad[-3:]],
        },
    )
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
