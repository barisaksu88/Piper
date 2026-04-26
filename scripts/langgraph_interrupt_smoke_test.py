from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.orchestrator_graph import (  # noqa: E402
    OrchestratorGraphContext,
    build_orchestrator_graph_runtime,
    load_langgraph_interrupt_record,
    run_agent_loop_with_langgraph,
    snapshot_orchestrator_state,
)
from core.orchestrator import OrchestratorConfig  # noqa: E402
from core.runtime_context import LATEST_RUNTIME_CONTEXT_PREFIX  # noqa: E402


class DummyUi:
    def __init__(self) -> None:
        self.events: list[object] = []

    def put(self, event) -> None:
        self.events.append(event)


class DummyChat:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def append_message(self, message: dict[str, object]) -> None:
        self.messages.append(dict(message))

    def upsert_hidden_system_message(self, prefix: str, content: str) -> None:
        marker = str(prefix or "").strip()
        if not marker:
            return
        payload = {"role": "system", "content": str(content or ""), "hidden": True}
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            role = str(message.get("role") or "").lower()
            content = str(message.get("content") or "")
            if role == "system" and content.startswith(marker):
                self.messages[index] = payload
                return
        self.messages.append(payload)


class DummyPromptContext:
    def build_runtime_context_message(self, _orc, *, reporter_just_ran: bool = False) -> str:
        pause = dict(getattr(_orc, "pending_stage_pause", {}) or {})
        pause_type = str(pause.get("pause_type") or "user input").replace("_", " ")
        suffix = "reporter" if reporter_just_ran else "manager"
        return f"{LATEST_RUNTIME_CONTEXT_PREFIX}\nLatest stage: {suffix} paused for {pause_type}."


class InterruptDummyOrchestrator:
    def __init__(self) -> None:
        self.ui = DummyUi()
        self.turn_stats = SimpleNamespace(turn_id="interrupt-smoke")
        self.next_stage = "PERSONA"
        self.user_msg = "Interrupt before speaking."
        self.route_decision = {"decision": "CHAT"}
        self.context_card = {}
        self.scratchpad: list[str] = []
        self.ingested_document_chat = False
        self.document_focus_text = ""
        self.document_focus_refs: list[str] = []
        self.document_focus_sources: list[str] = []
        self.turn_screen_image_path = None
        self.turn_screen_image_kind = ""
        self.latest_codex_escalation = None
        self.failed_task_router_retries = 0
        self.last_stage_outcome = None
        self.last_verification = None
        self.route_interceptor = ""
        self.reporter_just_ran = False
        self.latest_search_summary = ""
        self.latest_search_failed = False
        self.latest_search_error = ""
        self.synthetic_user_turn = False
        self.is_search_result = False
        self.pending_file_target_confirmation = None
        self.pending_stage_pause = None
        self.calls: list[str] = []

    def dispatch_stage(self, stage_name: str) -> None:
        self.calls.append(stage_name)
        if stage_name != "PERSONA":
            raise RuntimeError(f"Unexpected interrupt smoke stage: {stage_name}")
        self.scratchpad.append("PERSONA: resumed after interrupt")
        self.next_stage = "FINISHED"


class FileTargetInterruptOrchestrator(InterruptDummyOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.next_stage = "MANAGER"
        self.user_msg = "Delete b.txt."
        self.route_decision = {
            "decision": "TASK",
            "card": {
                "goal": "Delete b.txt.",
                "stages": [
                    {
                        "stage_goal": "Delete b.txt.",
                        "stage_type": "FILE_WORK",
                        "success_condition": "The target file is deleted.",
                        "active_targets": ["b.txt"],
                        "declared_exact_targets": ["b.txt"],
                    }
                ],
            },
        }
        self.confirmed_target = ""

    def dispatch_stage(self, stage_name: str) -> None:
        self.calls.append(stage_name)
        if stage_name != "MANAGER":
            raise RuntimeError(f"Unexpected file-target interrupt stage: {stage_name}")
        stages = list(((self.route_decision.get("card") or {}).get("stages") or []))
        stage = dict(stages[0] if stages else {})
        active_targets = [str(item) for item in (stage.get("active_targets") or [])]
        if "notes/b.txt" in active_targets:
            self.confirmed_target = "notes/b.txt"
            self.scratchpad.append("MANAGER: confirmed target")
            self.next_stage = "FINISHED"
            self.pending_file_target_confirmation = None
            return
        self.pending_file_target_confirmation = {
            "kind": "missing_file_target_confirmation",
            "exact_target": "b.txt",
            "candidates": ["notes/b.txt"],
            "question": "I can't find `b.txt`. Did you mean `notes/b.txt`?",
            "route_decision": self.route_decision,
            "stage_type": "FILE_WORK",
        }
        self.scratchpad.append("MANAGER: needs file-target confirmation")
        self.next_stage = "PERSONA"


class StageUserInputPauseOrchestrator(InterruptDummyOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.next_stage = "MANAGER"
        self.user_msg = "Plan a tiny project."
        self.route_decision = {
            "decision": "TASK",
            "card": {
                "goal": "Plan a tiny project.",
                "stages": [
                    {
                        "stage_goal": "Ask the user which folder to use.",
                        "stage_type": "CHAT",
                        "success_condition": "Await user input with the selected folder.",
                    }
                ],
            },
        }
        self.resumed_user_msg = ""

    def dispatch_stage(self, stage_name: str) -> None:
        self.calls.append(stage_name)
        if stage_name == "MANAGER":
            self.pending_stage_pause = {
                "kind": "stage_pause",
                "pause_type": "user_input",
                "question": "Which folder should I use?",
                "stage_num": 1,
                "total_stages": 1,
                "stage_type": "CHAT",
                "stage_goal": "Ask the user which folder to use.",
                "route_decision": self.route_decision,
            }
            self.scratchpad.append("MANAGER: needs user input")
            self.next_stage = "PERSONA"
            return
        if stage_name == "ROUTE":
            self.resumed_user_msg = str(self.user_msg or "")
            self.route_decision = {"decision": "CHAT", "card": {"query": self.user_msg}}
            self.scratchpad.append(f"ROUTE: resumed with {self.user_msg}")
            self.next_stage = "PERSONA"
            return
        if stage_name == "PERSONA":
            self.scratchpad.append("PERSONA: answered resumed pause")
            self.next_stage = "FINISHED"
            return
        raise RuntimeError(f"Unexpected stage-user-input interrupt stage: {stage_name}")


class StageApprovalPauseOrchestrator(InterruptDummyOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.next_stage = "MANAGER"
        self.user_msg = "Inspect and organize notes after approval."
        self.route_decision = {
            "decision": "TASK",
            "card": {
                "goal": "Inspect and organize notes after approval.",
                "stages": [
                    {
                        "stage_goal": "Present a notes organization plan for approval.",
                        "stage_type": "FILE_WORK",
                        "success_condition": "The proposal is ready for user approval before executing.",
                    },
                    {
                        "stage_goal": "Apply the approved notes organization plan.",
                        "stage_type": "FILE_WORK",
                        "success_condition": "The approved organization changes are complete.",
                    },
                ],
            },
        }
        self.approved_executed = False
        self.persona_notice_kind = ""

    def dispatch_stage(self, stage_name: str) -> None:
        self.calls.append(stage_name)
        if stage_name == "MANAGER":
            stages = [dict(item) for item in ((self.route_decision.get("card") or {}).get("stages") or [])]
            first_goal = str((stages[0] if stages else {}).get("stage_goal") or "")
            if first_goal.startswith("Apply the approved"):
                self.approved_executed = True
                self.scratchpad.append("MANAGER: approved plan executed")
                self.next_stage = "FINISHED"
                return
            self.pending_stage_pause = {
                "kind": "stage_pause",
                "pause_type": "approval",
                "question": "Move notes into topic folders?",
                "stage_num": 1,
                "total_stages": 2,
                "stage_type": "FILE_WORK",
                "stage_goal": "Present a notes organization plan for approval.",
                "route_decision": self.route_decision,
                "approved_route_decision": {
                    "decision": "TASK",
                    "card": {
                        "goal": "Inspect and organize notes after approval.",
                        "stages": [stages[1]],
                    },
                },
                "approval_resume_mode": "after_stage",
            }
            self.scratchpad.append("MANAGER: approval proposal ready")
            self.next_stage = "PERSONA"
            return
        if stage_name == "PERSONA":
            self.persona_notice_kind = str(
                ((self.route_decision.get("system_notice") or {}).get("kind") or "")
            )
            self.scratchpad.append(f"PERSONA: {self.persona_notice_kind}")
            self.next_stage = "FINISHED"
            return
        raise RuntimeError(f"Unexpected stage-approval interrupt stage: {stage_name}")


class EntryPointStageUserInputInterruptOrchestrator(StageUserInputPauseOrchestrator):
    instances: list["EntryPointStageUserInputInterruptOrchestrator"] = []

    def __init__(self, cfg) -> None:
        super().__init__()
        self.ui = cfg.ui if cfg.ui is not None else DummyUi()
        self.chat = cfg.chat if cfg.chat is not None else DummyChat()
        self.prompt_context = cfg.prompt_context if cfg.prompt_context is not None else DummyPromptContext()
        self.turn_stats = SimpleNamespace(turn_id="entrypoint-stage-user-input-interrupt-smoke")
        self._turn_stats_recorded = False
        EntryPointStageUserInputInterruptOrchestrator.instances.append(self)

    def prepare_turn(self) -> None:
        self.next_stage = "MANAGER"
        self.user_msg = "Plan a tiny project."
        self.scratchpad = []
        self.context_card = {}
        self.pending_stage_pause = None
        self.turn_stats = SimpleNamespace(turn_id="entrypoint-stage-user-input-interrupt-smoke")

    def _record_turn_stats_if_ready(self, *, aborted: bool = False, detail: str = "", phase: str = "") -> None:
        self._turn_stats_recorded = True

    def _log_dashboard(self, text: str) -> None:
        self.ui.put(("status_widget_dashboard_activity", text))


class EntryPointStageApprovalInterruptOrchestrator(StageApprovalPauseOrchestrator):
    instances: list["EntryPointStageApprovalInterruptOrchestrator"] = []

    def __init__(self, cfg) -> None:
        super().__init__()
        self.ui = cfg.ui if cfg.ui is not None else DummyUi()
        self.chat = cfg.chat if cfg.chat is not None else DummyChat()
        self.prompt_context = cfg.prompt_context if cfg.prompt_context is not None else DummyPromptContext()
        self.turn_stats = SimpleNamespace(turn_id="entrypoint-stage-approval-interrupt-smoke")
        self._turn_stats_recorded = False
        EntryPointStageApprovalInterruptOrchestrator.instances.append(self)

    def prepare_turn(self) -> None:
        self.next_stage = "MANAGER"
        self.user_msg = "Inspect and organize notes after approval."
        self.scratchpad = []
        self.context_card = {}
        self.pending_stage_pause = None
        self.turn_stats = SimpleNamespace(turn_id="entrypoint-stage-approval-interrupt-smoke")

    def _record_turn_stats_if_ready(self, *, aborted: bool = False, detail: str = "", phase: str = "") -> None:
        self._turn_stats_recorded = True

    def _log_dashboard(self, text: str) -> None:
        self.ui.put(("status_widget_dashboard_activity", text))


def run_basic_interrupt_smoke() -> dict:
    thread_id = "interrupt-smoke"
    with TemporaryDirectory() as tmp_dir:
        checkpoint_path = Path(tmp_dir) / "langgraph_interrupt.sqlite"
        runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=50,
        )
        first_orc = InterruptDummyOrchestrator()
        try:
            initial_state = snapshot_orchestrator_state(first_orc, stage_trace=[])
            initial_state["interrupt_before_stage"] = "PERSONA"
            initial_state["interrupt_payload"] = {
                "kind": "approval",
                "question": "Allow PERSONA to continue?",
                "next_stage": "PERSONA",
            }
            first_result = runtime.graph.invoke(
                initial_state,
                config={"configurable": {"thread_id": thread_id}},
                context=OrchestratorGraphContext(orchestrator=first_orc),
            )
            first_snapshot = runtime.graph.get_state({"configurable": {"thread_id": thread_id}})
        finally:
            runtime.close()

        resumed_runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=50,
        )
        resumed_orc = InterruptDummyOrchestrator()
        try:
            from langgraph.types import Command

            final_result = resumed_runtime.graph.invoke(
                Command(resume={"approved": True, "source": "smoke"}),
                config={"configurable": {"thread_id": thread_id}},
                context=OrchestratorGraphContext(orchestrator=resumed_orc),
            )
            final_snapshot = resumed_runtime.graph.get_state({"configurable": {"thread_id": thread_id}})
        finally:
            resumed_runtime.close()

        interrupts = list(first_result.get("__interrupt__") or []) if isinstance(first_result, dict) else []
        interrupt_values = [getattr(item, "value", None) for item in interrupts]
        success = bool(
            interrupts
            and interrupt_values[0].get("kind") == "approval"
            and first_orc.calls == []
            and tuple(getattr(first_snapshot, "next", ()) or ()) == ("await_interrupt",)
            and resumed_orc.calls == ["PERSONA"]
            and final_result.get("next_stage") == "FINISHED"
            and final_result.get("stage_trace") == ["PERSONA"]
            and final_result.get("interrupt_resume_value") == {"approved": True, "source": "smoke"}
            and tuple(getattr(final_snapshot, "next", ()) or ()) == ()
        )
        return {
            "success": success,
            "first_interrupt_values": interrupt_values,
            "first_stage_calls": first_orc.calls,
            "first_checkpoint_next": list(getattr(first_snapshot, "next", ()) or ()),
            "resume_stage_calls": resumed_orc.calls,
            "final_next_stage": final_result.get("next_stage"),
            "final_stage_trace": final_result.get("stage_trace") or [],
            "interrupt_resume_value": final_result.get("interrupt_resume_value"),
            "final_checkpoint_next": list(getattr(final_snapshot, "next", ()) or ()),
        }


def run_stage_user_input_interrupt_smoke() -> dict:
    thread_id = "stage-user-input-interrupt-smoke"
    with TemporaryDirectory() as tmp_dir:
        checkpoint_path = Path(tmp_dir) / "langgraph_stage_user_input_interrupt.sqlite"
        runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=50,
        )
        first_orc = StageUserInputPauseOrchestrator()
        try:
            first_result = runtime.graph.invoke(
                snapshot_orchestrator_state(first_orc, stage_trace=[]),
                config={"configurable": {"thread_id": thread_id}},
                context=OrchestratorGraphContext(orchestrator=first_orc),
            )
            first_snapshot = runtime.graph.get_state({"configurable": {"thread_id": thread_id}})
        finally:
            runtime.close()

        resumed_runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=50,
        )
        resumed_orc = StageUserInputPauseOrchestrator()
        try:
            from langgraph.types import Command

            final_result = resumed_runtime.graph.invoke(
                Command(resume={"user_msg": "use notes"}),
                config={"configurable": {"thread_id": thread_id}},
                context=OrchestratorGraphContext(orchestrator=resumed_orc),
            )
            final_snapshot = resumed_runtime.graph.get_state({"configurable": {"thread_id": thread_id}})
        finally:
            resumed_runtime.close()

        interrupts = list(first_result.get("__interrupt__") or []) if isinstance(first_result, dict) else []
        interrupt_values = [getattr(item, "value", None) for item in interrupts]
        success = bool(
            interrupts
            and interrupt_values[0].get("kind") == "stage_user_input_pause"
            and first_orc.calls == ["MANAGER"]
            and tuple(getattr(first_snapshot, "next", ()) or ()) == ("await_interrupt",)
            and resumed_orc.calls == ["ROUTE", "PERSONA"]
            and resumed_orc.resumed_user_msg == "use notes"
            and final_result.get("next_stage") == "FINISHED"
            and final_result.get("stage_trace") == ["MANAGER", "ROUTE", "PERSONA"]
            and tuple(getattr(final_snapshot, "next", ()) or ()) == ()
        )
        return {
            "success": success,
            "first_interrupt_values": interrupt_values,
            "first_stage_calls": first_orc.calls,
            "first_checkpoint_next": list(getattr(first_snapshot, "next", ()) or ()),
            "resume_stage_calls": resumed_orc.calls,
            "resumed_user_msg": resumed_orc.resumed_user_msg,
            "final_next_stage": final_result.get("next_stage"),
            "final_stage_trace": final_result.get("stage_trace") or [],
            "final_checkpoint_next": list(getattr(final_snapshot, "next", ()) or ()),
        }


def _start_stage_approval_pause(checkpoint_path: Path, *, thread_id: str) -> dict:
    runtime = build_orchestrator_graph_runtime(
        with_checkpointer=True,
        checkpoint_mode="sqlite",
        checkpoint_path=checkpoint_path,
        checkpoint_history_limit=50,
    )
    first_orc = StageApprovalPauseOrchestrator()
    try:
        first_result = runtime.graph.invoke(
            snapshot_orchestrator_state(first_orc, stage_trace=[]),
            config={"configurable": {"thread_id": thread_id}},
            context=OrchestratorGraphContext(orchestrator=first_orc),
        )
        first_snapshot = runtime.graph.get_state({"configurable": {"thread_id": thread_id}})
    finally:
        runtime.close()
    return {
        "first_orc": first_orc,
        "first_result": first_result,
        "first_snapshot": first_snapshot,
    }


def run_stage_approval_interrupt_smoke() -> dict:
    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        approve_thread_id = "stage-approval-interrupt-approve-smoke"
        approve_checkpoint_path = tmp_path / "stage_approval_interrupt_approve.sqlite"
        approve_start = _start_stage_approval_pause(approve_checkpoint_path, thread_id=approve_thread_id)
        approve_runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=approve_checkpoint_path,
            checkpoint_history_limit=50,
        )
        approve_orc = StageApprovalPauseOrchestrator()
        try:
            from langgraph.types import Command

            approve_result = approve_runtime.graph.invoke(
                Command(resume={"user_msg": "yes"}),
                config={"configurable": {"thread_id": approve_thread_id}},
                context=OrchestratorGraphContext(orchestrator=approve_orc),
            )
            approve_snapshot = approve_runtime.graph.get_state({"configurable": {"thread_id": approve_thread_id}})
        finally:
            approve_runtime.close()

        decline_thread_id = "stage-approval-interrupt-decline-smoke"
        decline_checkpoint_path = tmp_path / "stage_approval_interrupt_decline.sqlite"
        decline_start = _start_stage_approval_pause(decline_checkpoint_path, thread_id=decline_thread_id)
        decline_runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=decline_checkpoint_path,
            checkpoint_history_limit=50,
        )
        decline_orc = StageApprovalPauseOrchestrator()
        try:
            from langgraph.types import Command

            decline_result = decline_runtime.graph.invoke(
                Command(resume={"user_msg": "no"}),
                config={"configurable": {"thread_id": decline_thread_id}},
                context=OrchestratorGraphContext(orchestrator=decline_orc),
            )
            decline_snapshot = decline_runtime.graph.get_state({"configurable": {"thread_id": decline_thread_id}})
        finally:
            decline_runtime.close()

        ambiguous_thread_id = "stage-approval-interrupt-ambiguous-smoke"
        ambiguous_checkpoint_path = tmp_path / "stage_approval_interrupt_ambiguous.sqlite"
        ambiguous_start = _start_stage_approval_pause(ambiguous_checkpoint_path, thread_id=ambiguous_thread_id)
        ambiguous_runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=ambiguous_checkpoint_path,
            checkpoint_history_limit=50,
        )
        ambiguous_orc = StageApprovalPauseOrchestrator()
        try:
            from langgraph.types import Command

            ambiguous_result = ambiguous_runtime.graph.invoke(
                Command(resume={"user_msg": "what changes exactly?"}),
                config={"configurable": {"thread_id": ambiguous_thread_id}},
                context=OrchestratorGraphContext(orchestrator=ambiguous_orc),
            )
            ambiguous_snapshot = ambiguous_runtime.graph.get_state({"configurable": {"thread_id": ambiguous_thread_id}})
        finally:
            ambiguous_runtime.close()

        approve_interrupts = (
            list(approve_start["first_result"].get("__interrupt__") or [])
            if isinstance(approve_start["first_result"], dict)
            else []
        )
        approve_interrupt_values = [getattr(item, "value", None) for item in approve_interrupts]
        decline_interrupts = (
            list(decline_start["first_result"].get("__interrupt__") or [])
            if isinstance(decline_start["first_result"], dict)
            else []
        )
        decline_interrupt_values = [getattr(item, "value", None) for item in decline_interrupts]
        ambiguous_interrupts = list(ambiguous_result.get("__interrupt__") or []) if isinstance(ambiguous_result, dict) else []
        ambiguous_interrupt_values = [getattr(item, "value", None) for item in ambiguous_interrupts]

        approve_ok = bool(
            approve_interrupt_values
            and approve_interrupt_values[0].get("kind") == "stage_approval_pause"
            and approve_start["first_orc"].calls == ["MANAGER"]
            and tuple(getattr(approve_start["first_snapshot"], "next", ()) or ()) == ("await_interrupt",)
            and approve_orc.calls == ["MANAGER"]
            and approve_orc.approved_executed
            and approve_result.get("next_stage") == "FINISHED"
            and approve_result.get("stage_trace") == ["MANAGER", "MANAGER"]
            and tuple(getattr(approve_snapshot, "next", ()) or ()) == ()
        )
        decline_ok = bool(
            decline_interrupt_values
            and decline_interrupt_values[0].get("kind") == "stage_approval_pause"
            and decline_start["first_orc"].calls == ["MANAGER"]
            and decline_orc.calls == ["PERSONA"]
            and decline_orc.persona_notice_kind == "stage_approval_cancelled"
            and decline_result.get("next_stage") == "FINISHED"
            and decline_result.get("stage_trace") == ["MANAGER", "PERSONA"]
            and tuple(getattr(decline_snapshot, "next", ()) or ()) == ()
        )
        ambiguous_ok = bool(
            ambiguous_interrupt_values
            and ambiguous_interrupt_values[0].get("kind") == "stage_approval_pause"
            and "Please answer yes/no" in str(ambiguous_interrupt_values[0].get("question") or "")
            and ambiguous_orc.calls == []
            and tuple(getattr(ambiguous_snapshot, "next", ()) or ()) == ("await_interrupt",)
        )

        return {
            "success": bool(approve_ok and decline_ok and ambiguous_ok),
            "approve": {
                "success": approve_ok,
                "first_interrupt_values": approve_interrupt_values,
                "first_stage_calls": approve_start["first_orc"].calls,
                "first_checkpoint_next": list(getattr(approve_start["first_snapshot"], "next", ()) or ()),
                "resume_stage_calls": approve_orc.calls,
                "approved_executed": approve_orc.approved_executed,
                "final_stage_trace": approve_result.get("stage_trace") or [],
                "final_checkpoint_next": list(getattr(approve_snapshot, "next", ()) or ()),
            },
            "decline": {
                "success": decline_ok,
                "first_interrupt_values": decline_interrupt_values,
                "first_stage_calls": decline_start["first_orc"].calls,
                "resume_stage_calls": decline_orc.calls,
                "persona_notice_kind": decline_orc.persona_notice_kind,
                "final_stage_trace": decline_result.get("stage_trace") or [],
                "final_checkpoint_next": list(getattr(decline_snapshot, "next", ()) or ()),
            },
            "ambiguous": {
                "success": ambiguous_ok,
                "interrupt_values": ambiguous_interrupt_values,
                "resume_stage_calls": ambiguous_orc.calls,
                "checkpoint_next": list(getattr(ambiguous_snapshot, "next", ()) or ()),
            },
        }


def run_entrypoint_stage_user_input_interrupt_smoke() -> dict:
    import core.orchestrator_graph as graph_module

    with TemporaryDirectory() as tmp_dir:
        checkpoint_path = Path(tmp_dir) / "entrypoint_stage_user_input_interrupt.sqlite"
        interrupt_path = Path(tmp_dir) / "entrypoint_stage_user_input_interrupt.json"
        ui = DummyUi()
        chat = DummyChat()
        prompt_context = DummyPromptContext()

        old_checkpoint_path = os.environ.get("PIPER_LANGGRAPH_CHECKPOINT_PATH")
        old_interrupt_path = os.environ.get("PIPER_LANGGRAPH_INTERRUPT_PATH")
        old_debug_trace = bool(getattr(graph_module.CFG, "DEBUG_LANGGRAPH_TRACE", True))
        old_orchestrator = graph_module.Orchestrator
        EntryPointStageUserInputInterruptOrchestrator.instances = []
        os.environ["PIPER_LANGGRAPH_CHECKPOINT_PATH"] = str(checkpoint_path)
        os.environ["PIPER_LANGGRAPH_INTERRUPT_PATH"] = str(interrupt_path)
        graph_module.CFG.update({"DEBUG_LANGGRAPH_TRACE": False})
        graph_module.Orchestrator = EntryPointStageUserInputInterruptOrchestrator
        try:
            cfg = OrchestratorConfig(
                llm=None,
                brain=None,
                knowledge=None,
                prompt_context=prompt_context,
                chat=chat,
                styles=None,
                pipeline=None,
                ui=ui,
                get_context=lambda: chat.messages,
                boot=None,
                img_gen=None,
            )
            run_agent_loop_with_langgraph(cfg)
        finally:
            graph_module.Orchestrator = old_orchestrator
            graph_module.CFG.update({"DEBUG_LANGGRAPH_TRACE": old_debug_trace})
            if old_checkpoint_path is None:
                os.environ.pop("PIPER_LANGGRAPH_CHECKPOINT_PATH", None)
            else:
                os.environ["PIPER_LANGGRAPH_CHECKPOINT_PATH"] = old_checkpoint_path
            if old_interrupt_path is None:
                os.environ.pop("PIPER_LANGGRAPH_INTERRUPT_PATH", None)
            else:
                os.environ["PIPER_LANGGRAPH_INTERRUPT_PATH"] = old_interrupt_path

        instance = (
            EntryPointStageUserInputInterruptOrchestrator.instances[-1]
            if EntryPointStageUserInputInterruptOrchestrator.instances
            else None
        )
        record = load_langgraph_interrupt_record(path=interrupt_path)
        runtime_context_messages = [
            dict(message)
            for message in chat.messages
            if str(message.get("role") or "").lower() == "system"
            and str(message.get("content") or "").startswith(LATEST_RUNTIME_CONTEXT_PREFIX)
        ]
        stream_text = "".join(
            str(payload.get("text") or "")
            for event_name, payload in ui.events
            if event_name == "assistant_stream_delta" and isinstance(payload, dict)
        )
        success = bool(
            instance is not None
            and instance.calls == ["MANAGER"]
            and not getattr(instance, "_turn_stats_recorded", False)
            and checkpoint_path.exists()
            and record.get("status") == "pending"
            and record.get("thread_id") == "entrypoint-stage-user-input-interrupt-smoke"
            and (record.get("checkpoint_next") or []) == ["await_interrupt"]
            and (record.get("stage_trace") or []) == ["MANAGER"]
            and dict(record.get("interrupt_payload") or {}).get("kind") == "stage_user_input_pause"
            and "Which folder should I use?" in stream_text
            and len(runtime_context_messages) == 1
            and runtime_context_messages[0].get("hidden") is True
        )
        return {
            "success": success,
            "calls": list(instance.calls if instance else []),
            "stats_recorded": bool(getattr(instance, "_turn_stats_recorded", False)),
            "checkpoint_exists": checkpoint_path.exists(),
            "interrupt_record": record,
            "runtime_context_messages": runtime_context_messages,
            "stream_text": stream_text,
            "ui_event_names": [str(event[0]) for event in ui.events],
        }


def run_entrypoint_stage_approval_interrupt_smoke() -> dict:
    import core.orchestrator_graph as graph_module

    with TemporaryDirectory() as tmp_dir:
        checkpoint_path = Path(tmp_dir) / "entrypoint_stage_approval_interrupt.sqlite"
        interrupt_path = Path(tmp_dir) / "entrypoint_stage_approval_interrupt.json"
        ui = DummyUi()
        chat = DummyChat()
        prompt_context = DummyPromptContext()

        old_checkpoint_path = os.environ.get("PIPER_LANGGRAPH_CHECKPOINT_PATH")
        old_interrupt_path = os.environ.get("PIPER_LANGGRAPH_INTERRUPT_PATH")
        old_debug_trace = bool(getattr(graph_module.CFG, "DEBUG_LANGGRAPH_TRACE", True))
        old_orchestrator = graph_module.Orchestrator
        EntryPointStageApprovalInterruptOrchestrator.instances = []
        os.environ["PIPER_LANGGRAPH_CHECKPOINT_PATH"] = str(checkpoint_path)
        os.environ["PIPER_LANGGRAPH_INTERRUPT_PATH"] = str(interrupt_path)
        graph_module.CFG.update({"DEBUG_LANGGRAPH_TRACE": False})
        graph_module.Orchestrator = EntryPointStageApprovalInterruptOrchestrator
        try:
            cfg = OrchestratorConfig(
                llm=None,
                brain=None,
                knowledge=None,
                prompt_context=prompt_context,
                chat=chat,
                styles=None,
                pipeline=None,
                ui=ui,
                get_context=lambda: chat.messages,
                boot=None,
                img_gen=None,
            )
            run_agent_loop_with_langgraph(cfg)
        finally:
            graph_module.Orchestrator = old_orchestrator
            graph_module.CFG.update({"DEBUG_LANGGRAPH_TRACE": old_debug_trace})
            if old_checkpoint_path is None:
                os.environ.pop("PIPER_LANGGRAPH_CHECKPOINT_PATH", None)
            else:
                os.environ["PIPER_LANGGRAPH_CHECKPOINT_PATH"] = old_checkpoint_path
            if old_interrupt_path is None:
                os.environ.pop("PIPER_LANGGRAPH_INTERRUPT_PATH", None)
            else:
                os.environ["PIPER_LANGGRAPH_INTERRUPT_PATH"] = old_interrupt_path

        instance = (
            EntryPointStageApprovalInterruptOrchestrator.instances[-1]
            if EntryPointStageApprovalInterruptOrchestrator.instances
            else None
        )
        record = load_langgraph_interrupt_record(path=interrupt_path)
        runtime_context_messages = [
            dict(message)
            for message in chat.messages
            if str(message.get("role") or "").lower() == "system"
            and str(message.get("content") or "").startswith(LATEST_RUNTIME_CONTEXT_PREFIX)
        ]
        stream_text = "".join(
            str(payload.get("text") or "")
            for event_name, payload in ui.events
            if event_name == "assistant_stream_delta" and isinstance(payload, dict)
        )
        success = bool(
            instance is not None
            and instance.calls == ["MANAGER"]
            and not getattr(instance, "_turn_stats_recorded", False)
            and checkpoint_path.exists()
            and record.get("status") == "pending"
            and record.get("thread_id") == "entrypoint-stage-approval-interrupt-smoke"
            and (record.get("checkpoint_next") or []) == ["await_interrupt"]
            and (record.get("stage_trace") or []) == ["MANAGER"]
            and dict(record.get("interrupt_payload") or {}).get("kind") == "stage_approval_pause"
            and "Move notes into topic folders?" in stream_text
            and len(runtime_context_messages) == 1
            and runtime_context_messages[0].get("hidden") is True
            and "paused for approval" in str(runtime_context_messages[0].get("content") or "")
        )
        return {
            "success": success,
            "calls": list(instance.calls if instance else []),
            "stats_recorded": bool(getattr(instance, "_turn_stats_recorded", False)),
            "checkpoint_exists": checkpoint_path.exists(),
            "interrupt_record": record,
            "runtime_context_messages": runtime_context_messages,
            "stream_text": stream_text,
            "ui_event_names": [str(event[0]) for event in ui.events],
        }


def run_file_target_interrupt_smoke() -> dict:
    thread_id = "file-target-interrupt-smoke"
    with TemporaryDirectory() as tmp_dir:
        checkpoint_path = Path(tmp_dir) / "langgraph_file_target_interrupt.sqlite"
        runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=50,
        )
        first_orc = FileTargetInterruptOrchestrator()
        try:
            first_result = runtime.graph.invoke(
                snapshot_orchestrator_state(first_orc, stage_trace=[]),
                config={"configurable": {"thread_id": thread_id}},
                context=OrchestratorGraphContext(orchestrator=first_orc),
            )
            first_snapshot = runtime.graph.get_state({"configurable": {"thread_id": thread_id}})
        finally:
            runtime.close()

        resumed_runtime = build_orchestrator_graph_runtime(
            with_checkpointer=True,
            checkpoint_mode="sqlite",
            checkpoint_path=checkpoint_path,
            checkpoint_history_limit=50,
        )
        resumed_orc = FileTargetInterruptOrchestrator()
        try:
            from langgraph.types import Command

            final_result = resumed_runtime.graph.invoke(
                Command(resume={"user_msg": "yes"}),
                config={"configurable": {"thread_id": thread_id}},
                context=OrchestratorGraphContext(orchestrator=resumed_orc),
            )
            final_snapshot = resumed_runtime.graph.get_state({"configurable": {"thread_id": thread_id}})
        finally:
            resumed_runtime.close()

        interrupts = list(first_result.get("__interrupt__") or []) if isinstance(first_result, dict) else []
        interrupt_values = [getattr(item, "value", None) for item in interrupts]
        success = bool(
            interrupts
            and interrupt_values[0].get("kind") == "missing_file_target_confirmation"
            and first_orc.calls == ["MANAGER"]
            and tuple(getattr(first_snapshot, "next", ()) or ()) == ("await_interrupt",)
            and resumed_orc.calls == ["MANAGER"]
            and resumed_orc.confirmed_target == "notes/b.txt"
            and final_result.get("next_stage") == "FINISHED"
            and final_result.get("stage_trace") == ["MANAGER", "MANAGER"]
            and tuple(getattr(final_snapshot, "next", ()) or ()) == ()
        )
        return {
            "success": success,
            "first_interrupt_values": interrupt_values,
            "first_stage_calls": first_orc.calls,
            "first_checkpoint_next": list(getattr(first_snapshot, "next", ()) or ()),
            "resume_stage_calls": resumed_orc.calls,
            "confirmed_target": resumed_orc.confirmed_target,
            "final_next_stage": final_result.get("next_stage"),
            "final_stage_trace": final_result.get("stage_trace") or [],
            "final_checkpoint_next": list(getattr(final_snapshot, "next", ()) or ()),
        }


def main() -> int:
    basic = run_basic_interrupt_smoke()
    stage_user_input = run_stage_user_input_interrupt_smoke()
    stage_approval = run_stage_approval_interrupt_smoke()
    entrypoint_stage_user_input = run_entrypoint_stage_user_input_interrupt_smoke()
    entrypoint_stage_approval = run_entrypoint_stage_approval_interrupt_smoke()
    file_target = run_file_target_interrupt_smoke()
    report = {
        "success": bool(
            basic.get("success")
            and stage_user_input.get("success")
            and stage_approval.get("success")
            and entrypoint_stage_user_input.get("success")
            and entrypoint_stage_approval.get("success")
            and file_target.get("success")
        ),
        "basic": basic,
        "stage_user_input": stage_user_input,
        "stage_approval": stage_approval,
        "entrypoint_stage_user_input": entrypoint_stage_user_input,
        "entrypoint_stage_approval": entrypoint_stage_approval,
        "file_target": file_target,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
