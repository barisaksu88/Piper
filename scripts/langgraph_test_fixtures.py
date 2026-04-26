from __future__ import annotations

from types import SimpleNamespace

from core.runtime_context import LATEST_RUNTIME_CONTEXT_PREFIX


class DummyUi:
    """Minimal UI sink for LangGraph smoke tests."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def put(self, event) -> None:
        self.events.append(event)


class DummyChat:
    """Minimal chat store with hidden-system-message upserts."""

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
            msg_content = str(message.get("content") or "")
            if role == "system" and msg_content.startswith(marker):
                self.messages[index] = payload
                return
        self.messages.append(payload)


class DummyPromptContext:
    """Minimal prompt-context helper for interrupt/runtime tests."""

    def build_runtime_context_message(self, _orc, *, reporter_just_ran: bool = False) -> str:
        pause = dict(getattr(_orc, "pending_stage_pause", {}) or {})
        pause_type = str(pause.get("pause_type") or "user input").replace("_", " ")
        suffix = "reporter" if reporter_just_ran else "manager"
        return f"{LATEST_RUNTIME_CONTEXT_PREFIX}\nLatest stage: {suffix} paused for {pause_type}."


class BaseDummyOrchestrator:
    """Common orchestrator stub for LangGraph smoke tests.

    Provides the minimal surface area shared by all LangGraph test
    orchestrators.  Subclass and override only what the specific test
    needs.
    """

    def __init__(self, *, turn_id: str = "langgraph-smoke", user_msg: str = "") -> None:
        self.ui = DummyUi()
        self.turn_stats = SimpleNamespace(turn_id=turn_id)
        self.next_stage = "ROUTE"
        self.user_msg = user_msg
        self.route_decision: dict[str, object] = {}
        self.context_card: dict[str, object] = {}
        self.scratchpad: list[str] = []
        self.ingested_document_chat = False
        self.document_focus_text = ""
        self.document_focus_refs: list[str] = []
        self.document_focus_sources: list[str] = []
        self.turn_screen_image_path = None
        self.turn_screen_image_kind = ""
        self.failed_task_router_retries = 0
        self.last_stage_outcome = None
        self.last_verification = None
        self.route_interceptor = ""
        self.reporter_just_ran = False
        self.latest_search_summary = ""
        self.pending_stage_pause: dict[str, object] = {}
        self.skipped_file_targets: list[dict[str, object]] = []
        self.error_log: list[str] = []
        self.is_search_result = False
        self.latest_route_error = ""
        self.is_langgraph_turn = False
        self.langgraph_state_snapshot: dict[str, object] = {}
        self.latest_file_write_error: str = ""
        self.has_file_write_error = False
        self.turn_done = False
        self.screen_image_path = None
        self.screen_image_kind = ""

    # --- helpers used by interrupt tests ---

    def get_context(self) -> list[dict[str, object]]:
        return []

    def emit_runtime_signal(self, signal: dict[str, object]) -> None:
        self.ui.put(("runtime_signal", signal))

    def raise_if_cancelled(self) -> None:
        pass

    def _update_status(self, mode: str) -> None:
        self.ui.put(("status", mode))

    def _log_dashboard(self, text: str) -> None:
        self.ui.put(("dashboard", text))
