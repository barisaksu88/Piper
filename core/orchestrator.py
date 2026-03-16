"""core/orchestrator.py
The Executive Board Loop.
"""

from __future__ import annotations

from config import CFG
from core.contracts import EscalationDecision, RuntimeSignal
from core.engineering_support import EngineeringEscalationDetector
from core.orchestrator_phases import (
    phase_document_focus,
    phase_manager,
    phase_persona,
    phase_reporter,
    phase_route,
    phase_search,
)
from core.runtime_control import CancellationToken, OperationCancelled


class Orchestrator:
    """Manages the flow between Routing, Planning, and Speaking."""

    def __init__(self, llm_client, agent_brain, knowledge_mgr, style_mgr,
        chat_state, pipeline, ui_queue, get_context_fn, boot_mgr, img_gen,
                 prompt_context_service,
                 live_screen=None,
                 cancel_token: CancellationToken | None = None,
                 retain_cancel_token_fn=None,
                 release_cancel_token_fn=None):
        self.llm = llm_client
        self.brain = agent_brain
        self.knowledge = knowledge_mgr
        self.styles = style_mgr
        self.chat = chat_state
        self.pipeline = pipeline
        self.ui = ui_queue
        self.get_context = get_context_fn
        self.boot = boot_mgr
        self.img_gen = img_gen
        self.prompt_context = prompt_context_service
        self.live_screen = live_screen
        self.cancel_token = cancel_token
        self.retain_cancel_token = retain_cancel_token_fn or (lambda token: None)
        self.release_cancel_token = release_cancel_token_fn or (lambda token: None)

        self.ss = None
        self.temperature = 0.7
        self.knowledge_enabled = True
        self.next_stage = "ROUTE"

        self.route_decision = {}
        self.user_msg = ""
        self.is_search_result = False
        self.ingested_document_chat = False
        self.document_focus_text = ""
        self.document_focus_refs = []
        self.document_focus_sources = []
        self.turn_screen_image_path = None
        self.turn_screen_image_kind = ""
        self.scratchpad = []
        self.context_card = {}
        self.latest_codex_escalation: EscalationDecision | None = None
        self.engineering_support = EngineeringEscalationDetector(CFG.CODEX_ESCALATION_LOG_PATH)
        self.failed_task_router_retries = 0

    def _log_dashboard(self, text: str):
        self.ui.put(("status_widget_dashboard_activity", text))

    def _update_status(self, mode: str = "", goal: str = "", thought: str = "", step: str = ""):
        if mode:
            self.ui.put(("status_widget_mode", mode))
        self.ui.put(("status_widget_step", step))

    def emit_runtime_signal(self, signal: RuntimeSignal, *, scratchpad: list[str] | None = None) -> EscalationDecision | None:
        normalized = {
            "kind": str(signal.get("kind", "")).strip().lower(),
            "severity": str(signal.get("severity", "warning")).strip().lower() or "warning",
            "source": str(signal.get("source", "")).strip() or "runtime",
            "summary": str(signal.get("summary", "")).strip(),
            "details": str(signal.get("details", "")).strip(),
            "stage_goal": str(signal.get("stage_goal", "")).strip(),
            "stage_type": str(signal.get("stage_type", "")).strip(),
            "tool": str(signal.get("tool", "")).strip(),
            "count": int(signal.get("count", 0) or 0),
            "evidence_files": [str(item).strip() for item in (signal.get("evidence_files") or []) if str(item).strip()],
        }
        step = signal.get("step")
        if isinstance(step, int):
            normalized["step"] = step
        summary = normalized.get("summary") or normalized.get("kind") or "runtime signal"
        self.ui.put(("agent_log", f"[ENGINEERING SIGNAL] {summary}"))

        decision = self.engineering_support.record_signal(
            normalized,
            user_msg=self.user_msg,
            route_decision=self.route_decision,
            context_card=self.context_card,
            scratchpad=scratchpad if scratchpad is not None else self.scratchpad,
            history_tail=self.get_context()[-8:],
        )
        if decision:
            self.latest_codex_escalation = decision
            self.ui.put(("codex_escalation", decision))
            self._log_dashboard("Codex support brief prepared.")
        return decision

    def prepare_manual_codex_snapshot(
        self,
        *,
        note: str = "",
        source: str = "manual",
        monitor_text: str = "",
        dashboard_text: str = "",
        status_snapshot: str = "",
    ) -> EscalationDecision:
        decision = self.engineering_support.manual_snapshot(
            note=note,
            user_msg=self.user_msg,
            history_tail=self.get_context()[-8:],
            monitor_tail=monitor_text.splitlines(),
            dashboard_tail=dashboard_text.splitlines(),
            status_snapshot=status_snapshot,
            route_decision=self.route_decision,
            context_card=self.context_card,
            scratchpad=self.scratchpad,
            source=source,
        )
        self.latest_codex_escalation = decision
        self.ui.put(("codex_escalation", decision))
        return decision

    def raise_if_cancelled(self) -> None:
        if self.cancel_token is not None:
            self.cancel_token.raise_if_cancelled()

    def run(self):
        # Load style with config defaults as fallbacks.
        # StyleManager will use the style file's values if present, otherwise use these defaults.
        self.ss = self.styles.load(0.7, CFG.TTS_VOICE, CFG.TTS_SPEED)
        self.temperature = float(self.ss.temperature) if self.ss.temperature is not None else 0.7
        self.knowledge_enabled = getattr(self.ss, "knowledge", True)

        self.next_stage = "ROUTE"
        self.scratchpad = []
        self.context_card = {}
        self.route_decision = {}
        self.ingested_document_chat = False
        self.document_focus_text = ""
        self.document_focus_refs = []
        self.document_focus_sources = []
        self.turn_screen_image_path = None
        self.turn_screen_image_kind = ""
        self.latest_codex_escalation = None
        self.failed_task_router_retries = 0

        try:
            while self.next_stage != "FINISHED":
                self.raise_if_cancelled()
                if self.next_stage == "ROUTE":
                    self._phase_route()
                elif self.next_stage == "DOC_FOCUS":
                    self._phase_document_focus()
                elif self.next_stage == "SEARCH":
                    self._phase_search()
                elif self.next_stage == "REPORTER":
                    self._phase_reporter()
                elif self.next_stage == "MANAGER":
                    self._phase_manager()
                elif self.next_stage == "PERSONA":
                    self._phase_persona()
                else:
                    break
        except OperationCancelled:
            self.ui.put(("agent_log", "   -> Action canceled by user."))
            self._log_dashboard("Canceled.")
            raise

    def _phase_route(self):
        phase_route(self)

    def _phase_search(self):
        phase_search(self)

    def _phase_document_focus(self):
        phase_document_focus(self)

    def _phase_reporter(self):
        phase_reporter(self)

    def _phase_manager(self):
        phase_manager(self)

    def _phase_persona(self):
        phase_persona(self)


def run_agent_loop(
    llm_client,
    agent_brain,
    knowledge_mgr,
    style_mgr,
    chat_state,
    pipeline,
    ui_queue,
    get_current_context_fn,
    boot_mgr,
    img_gen,
    prompt_context_service,
    live_screen=None,
    cancel_token: CancellationToken | None = None,
    retain_cancel_token_fn=None,
    release_cancel_token_fn=None,
):
    orc = Orchestrator(
        llm_client, agent_brain, knowledge_mgr, style_mgr,
        chat_state, pipeline, ui_queue, get_current_context_fn, boot_mgr, img_gen,
        prompt_context_service=prompt_context_service,
        live_screen=live_screen,
        cancel_token=cancel_token,
        retain_cancel_token_fn=retain_cancel_token_fn,
        release_cancel_token_fn=release_cancel_token_fn,
    )
    orc.run()
