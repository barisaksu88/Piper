"""core/orchestrator.py
The Executive Board Loop.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from config import CFG
from core.contracts import EscalationDecision, RuntimeSignal
from core.engines.change_journal import ChangeJournal
from core.engines.conversation_compressor import ConversationCompressor
from core.engines.stats_collector import StatsCollector
from core.engineering_support import EngineeringEscalationDetector
from core.orchestrator_phases import (
    phase_document_focus,
    phase_manager,
    phase_persona,
    phase_reminder_set,
    phase_reporter,
    phase_route,
    phase_search,
    phase_undo,
)
from core.runtime_control import OperationCancelled
from core.engines import proactive_monitor as _proactive_monitor_registration  # noqa: F401


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrchestratorConfig:
    # -- Core LLM + Memory --
    llm: Any
    brain: Any
    knowledge: Any
    prompt_context: Any

    # -- Chat + Style --
    chat: Any
    styles: Any

    # -- Pipeline + UI --
    pipeline: Any
    ui: Any
    get_context: Any
    boot: Any

    # -- Tools --
    img_gen: Any

    # -- Live Screen --
    live_screen: Any | None = None

    # -- Search State (controller-owned lambdas) --
    cancel_token: Any | None = None
    retain_cancel_token: Any | None = None
    release_cancel_token: Any | None = None
    is_search_in_flight: Any | None = None
    retain_search_in_flight: Any | None = None
    release_search_in_flight: Any | None = None
    current_search_query: Any | None = None

    # -- Paths --
    conversation_summary_path: Path | None = None

    # -- LangGraph Recovery --
    langgraph_resume_thread_id: str = ""
    langgraph_resume_checkpoint_id: str = ""
    langgraph_resume_value: Any | None = None


class Orchestrator:
    """Manages the flow between Routing, Planning, and Speaking."""

    def __init__(self, cfg: OrchestratorConfig) -> None:
        self._cfg = cfg
        self.llm = cfg.llm
        self.brain = cfg.brain
        self.knowledge = cfg.knowledge
        self.prompt_context = cfg.prompt_context
        self.chat = cfg.chat
        self.styles = cfg.styles
        self.pipeline = cfg.pipeline
        self.ui = cfg.ui
        self.get_context = cfg.get_context
        self.boot = cfg.boot
        self.img_gen = cfg.img_gen
        self.live_screen = cfg.live_screen
        self.conversation_summary_path = cfg.conversation_summary_path or CFG.CONVERSATION_SUMMARY_PATH
        self.cancel_token = cfg.cancel_token
        self.retain_cancel_token = cfg.retain_cancel_token or (lambda token: None)
        self.release_cancel_token = cfg.release_cancel_token or (lambda token: None)
        self.is_search_in_flight = cfg.is_search_in_flight or (lambda: False)
        self.retain_search_in_flight = cfg.retain_search_in_flight or (lambda query="": None)
        self.release_search_in_flight = cfg.release_search_in_flight or (lambda: None)
        self.current_search_query = cfg.current_search_query or (lambda: "")

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
        self.last_stage_outcome = None
        self.last_verification = None
        self.latest_search_failed = False
        self.latest_search_error = ""
        self.conversation_compressor = ConversationCompressor()
        self.conversation_summary = self._load_conversation_summary()
        self.stats_collector = StatsCollector(CFG.STATS_PATH, CFG.STATS_ALERTS_PATH)
        self.stats_collector.startup_check_once()
        self.change_journal = ChangeJournal(CFG.CHANGE_JOURNAL_PATH)
        self.turn_stats = None
        self._turn_stats_recorded = False
        self.route_interceptor = ""
        self.undo_notice_pending = False
        self.last_change_journal_entry: dict | None = None
        self.synthetic_user_turn = False
        self.pending_file_target_confirmation: dict | None = None
        self.pending_stage_pause: dict | None = None
        # Tracks which style's bootstrap was last injected into history.
        # Bootstrap is prepended only on session start (empty) or style change.
        self._bootstrap_injected_for_style: str = ""

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

    def _load_conversation_summary(self) -> str:
        return self.conversation_compressor.load_summary(self.conversation_summary_path)

    def save_conversation_summary(self) -> None:
        self.conversation_compressor.save_summary(
            self.conversation_summary_path,
            self.conversation_summary,
        )

    def update_conversation_summary(self, summary: str) -> None:
        normalized = str(summary or "").strip()
        if normalized == str(self.conversation_summary or "").strip():
            return
        self.conversation_summary = normalized
        self.save_conversation_summary()

    def prepare_turn(self) -> None:
        """Reset per-turn orchestration state before phase dispatch begins."""
        _reloaded = CFG.reload_if_stale()
        if _reloaded:
            _LOG.info("Config hot-reloaded: %s", ", ".join(_reloaded))
            self.ui.put(("status_widget_dashboard_activity", f"Config reloaded: {', '.join(_reloaded)}"))

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
        self.last_stage_outcome = None
        self.last_verification = None
        self.latest_search_failed = False
        self.latest_search_error = ""
        self.turn_stats = self.stats_collector.resume_or_start_turn(
            cancel_token=self.cancel_token,
            fallback_owner=self.chat,
        )
        self._turn_stats_recorded = False
        self.route_interceptor = ""
        self.undo_notice_pending = False
        self.last_change_journal_entry = None
        self.synthetic_user_turn = False
        self.pending_file_target_confirmation = None
        self.pending_stage_pause = None

    def dispatch_stage(self, stage_name: str | None = None) -> str:
        """Execute exactly one top-level phase and return the dispatched stage name."""
        stage = str(stage_name or self.next_stage or "").strip().upper()
        if not stage:
            self.next_stage = "FINISHED"
            return ""

        if stage == "ROUTE":
            self._phase_route()
        elif stage == "DOC_FOCUS":
            self._phase_document_focus()
        elif stage == "SEARCH":
            self._phase_search()
        elif stage == "REPORTER":
            self._phase_reporter()
        elif stage == "MANAGER":
            self._phase_manager()
        elif stage == "UNDO":
            self._phase_undo()
        elif stage == "REMINDER_SET":
            self._phase_reminder_set()
        elif stage == "EXPLAIN":
            self._phase_explain()
        elif stage == "PERSONA":
            self._phase_persona()
        else:
            self.next_stage = "FINISHED"
        return stage

    def run(self):
        self.prepare_turn()

        try:
            while self.next_stage != "FINISHED":
                self.raise_if_cancelled()
                dispatched = self.dispatch_stage(self.next_stage)
                if not dispatched:
                    break
            self._record_turn_stats_if_ready()
        except OperationCancelled:
            self.ui.put(("agent_log", "   -> Action canceled by user."))
            self._log_dashboard("Canceled.")
            raise
        except Exception as exc:
            self._record_turn_stats_if_ready(aborted=True, detail=str(exc), phase=self.next_stage)
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

    def _phase_undo(self):
        phase_undo(self)

    def _phase_reminder_set(self):
        phase_reminder_set(self)

    def _phase_explain(self):
        phase_persona(self)

    def _phase_persona(self):
        phase_persona(self)

    def _record_turn_stats_if_ready(self, *, aborted: bool = False, detail: str = "", phase: str = "") -> None:
        if self._turn_stats_recorded:
            return
        if getattr(self.turn_stats, "record_deferred", False):
            return
        if aborted:
            record = self.stats_collector.record_aborted_turn(
                self.turn_stats,
                phase=phase,
                detail=detail,
            )
        else:
            record = self.stats_collector.record_turn(self.turn_stats)
        if record is None:
            return
        self._turn_stats_recorded = True
        self.ui.put(("stats_view_refresh", ""))


def run_agent_loop(orc_cfg: OrchestratorConfig) -> None:
    if CFG.LANGGRAPH_RUNTIME_ENABLED or str(getattr(orc_cfg, "langgraph_resume_thread_id", "") or "").strip():
        from core.orchestrator_graph import run_agent_loop_with_langgraph

        run_agent_loop_with_langgraph(orc_cfg)
        return
    orc = Orchestrator(orc_cfg)
    orc.run()
