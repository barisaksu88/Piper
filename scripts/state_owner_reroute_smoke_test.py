from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import core.orchestrator_phases as orchestrator_phases  # noqa: E402


@dataclass(frozen=True)
class StateOwnerRerouteSmokeReport:
    success: bool
    next_stage: str
    failed_task_router_retries: int
    hidden_system_messages: list[str]
    dashboard_messages: list[str]
    agent_log_tail: list[str]


class _FakeUI:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def put(self, event: tuple[str, str]) -> None:
        self.events.append((str(event[0]), str(event[1])))


class _FakeChat:
    def __init__(self) -> None:
        self.hidden_messages: list[str] = []

    def upsert_hidden_system_message(self, prefix: str, payload: str) -> None:
        self.hidden_messages.append(payload)


class _FakePromptContext:
    @staticmethod
    def build_runtime_context_message(_orc, *, reporter_just_ran: bool = False) -> str:
        del reporter_just_ran
        return (
            "[LATEST_RUNTIME_CONTEXT]\n"
            "previous_route: TASK\n"
            "previous_user_request: I'm not really working on that project to catch the stars, please remove it.\n"
            "task_goal: Delete the 'Catch the Stars' project entry from the user's task or project list.\n"
            "execution_status: FAILED / INCOMPLETE\n"
            "runtime_note: No pending tasks.\n"
        )

    @staticmethod
    def extract_latest_stage_proposal_answer(_stage_log) -> str:
        return ""

    @staticmethod
    def extract_exact_file_read_answer(_stage_log) -> str:
        return ""


class _FakeExecutor:
    def __init__(self, *_args, **_kwargs) -> None:
        self.pause_requested = False
        self.pause_mode = ""
        self.scratchpad: list[str] = []

    def run(self, _stage, _stage_num: int, _total_stages: int):
        stage_log = [
            "=== STAGE 1 START ===\n"
            "STAGE_GOAL: Delete the 'Catch the Stars' project entry from the user's task or project list.\n"
            "STAGE_TYPE: TASK_EVENT_WORK\n"
            "SUCCESS_CONDITION: The 'Catch the Stars' project is no longer present in the user's active tasks or project list.",
            "STEP 1\n"
            "THOUGHT: I need to list the current tasks to find the target before I can delete it.\n"
            "ACTION: [LIST_TASKS]\n"
            "OBSERVATION_KIND: info\n"
            "OBSERVATION_TEXT: No pending tasks.",
        ]
        self.scratchpad = list(stage_log)
        return True, stage_log


class _FakeOrc:
    def __init__(self) -> None:
        self.route_decision = {
            "decision": "TASK",
            "card": {
                "goal": "Delete the 'Catch the Stars' project entry from the user's task or project list.",
                "context": [
                    "The user previously stated he was working on a project called Catch the Stars.",
                    "The user has now explicitly requested to remove this project because he is no longer working on it.",
                ],
                "stages": [
                    {
                        "stage_goal": "Delete the 'Catch the Stars' project entry from the user's task or project list.",
                        "stage_type": "TASK_EVENT_WORK",
                        "success_condition": "The 'Catch the Stars' project is no longer present in the user's active tasks or project list.",
                    }
                ],
            },
        }
        self.context_card = {}
        self.ui = _FakeUI()
        self.chat = _FakeChat()
        self.prompt_context = _FakePromptContext()
        self.llm = None
        self.brain = None
        self.img_gen = None
        self.boot = None
        self.cancel_token = None
        self.scratchpad: list[str] = []
        self.failed_task_router_retries = 0
        self.next_stage = ""
        self.user_msg = "I'm not really working on that project to catch the stars, please remove it."

    def raise_if_cancelled(self) -> None:
        return None

    def _update_status(self, **_kwargs) -> None:
        return None

    def _log_dashboard(self, text: str) -> None:
        self.ui.put(("status_widget_dashboard_activity", text))

    def emit_runtime_signal(self, _signal, *, scratchpad=None):
        del scratchpad
        return None


def run_smoke() -> StateOwnerRerouteSmokeReport:
    fake_orc = _FakeOrc()
    original_executor = orchestrator_phases.StageExecutor
    orchestrator_phases.StageExecutor = _FakeExecutor
    try:
        orchestrator_phases.phase_manager(fake_orc)
    finally:
        orchestrator_phases.StageExecutor = original_executor

    dashboard_messages = [payload for kind, payload in fake_orc.ui.events if kind == "status_widget_dashboard_activity"]
    agent_log_tail = [payload for kind, payload in fake_orc.ui.events if kind == "agent_log"][-4:]
    success = (
        fake_orc.next_stage == "ROUTE"
        and fake_orc.failed_task_router_retries == 1
        and bool(fake_orc.chat.hidden_messages)
        and any("Auto-rerouting after failed stage" in item for item in agent_log_tail)
    )
    return StateOwnerRerouteSmokeReport(
        success=bool(success),
        next_stage=fake_orc.next_stage,
        failed_task_router_retries=int(fake_orc.failed_task_router_retries),
        hidden_system_messages=list(fake_orc.chat.hidden_messages),
        dashboard_messages=dashboard_messages,
        agent_log_tail=agent_log_tail,
    )


def main() -> int:
    report = run_smoke()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
