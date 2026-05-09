from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.graph_nodes import PiperState  # noqa: E402
from core.orchestrator_graph_builder import build_piper_graph  # noqa: E402


class _AnyMethod:
    """Accepts any call and returns None."""

    def __call__(self, *args, **kwargs):
        return None

    def __getattr__(self, name: str):
        return _AnyMethod()


class DummyChat:
    def get_messages_snapshot(self) -> list[dict]:
        return [{"role": "assistant", "content": "Hello."}]


class DummyUi:
    def __init__(self) -> None:
        self.events = []

    def put(self, event) -> None:
        self.events.append(event)


class BaseDummyOrchestrator:
    """Minimal dummy that satisfies the graph-node contract."""

    def __init__(self) -> None:
        self.ui = DummyUi()
        self.chat = DummyChat()
        self.stats_collector = _AnyMethod()
        self.next_stage = "ROUTE"
        self.route_decision: dict | None = None
        self.last_verification = None
        self.pending_file_target_confirmation = None
        self.pending_stage_pause = None
        self.scratchpad: list[str] = []
        self.user_msg = ""
        self.ss = SimpleNamespace(tts_voice="af_bella", tts_speed=1.0)
        self.turn_stats = SimpleNamespace(turn_id="smoke")

    def get_context(self) -> list[dict]:
        return []

    def _update_status(self, **kwargs) -> None:
        pass

    def _record_turn_stats_if_ready(self, **kwargs) -> None:
        pass

    def _log_dashboard(self, text: str) -> None:
        pass

    def emit_runtime_signal(self, payload: dict) -> None:
        pass


class ChatDummyOrchestrator(BaseDummyOrchestrator):
    """Simulates a CHAT route: ROUTE -> PERSONA -> FINISHED."""

    def dispatch_stage(self, stage_name: str) -> None:
        if stage_name == "PERSONA":
            self.next_stage = "FINISHED"
            self.scratchpad.append("PERSONA")
            return
        raise RuntimeError(f"Unexpected stage: {stage_name}")


class SearchDummyOrchestrator(BaseDummyOrchestrator):
    """Simulates a SEARCH route: ROUTE -> SEARCH -> FINISHED."""

    def dispatch_stage(self, stage_name: str) -> None:
        if stage_name == "SEARCH":
            self.next_stage = "FINISHED"
            self.scratchpad.append("SEARCH")
            return
        raise RuntimeError(f"Unexpected stage: {stage_name}")


class ExplainDummyOrchestrator(BaseDummyOrchestrator):
    """Simulates an EXPLAIN interceptor: ROUTE -> EXPLAIN -> PERSONA -> FINISHED."""

    def dispatch_stage(self, stage_name: str) -> None:
        if stage_name == "EXPLAIN":
            self.next_stage = "PERSONA"
            self.scratchpad.append("EXPLAIN")
            return
        if stage_name == "PERSONA":
            self.next_stage = "FINISHED"
            self.scratchpad.append("PERSONA")
            return
        raise RuntimeError(f"Unexpected stage: {stage_name}")


class TaskDummyOrchestrator(BaseDummyOrchestrator):
    """Simulates a TASK route: ROUTE -> MANAGER -> VERIFY -> PERSONA -> FINISHED."""

    def __init__(self) -> None:
        super().__init__()
        self.last_verification = SimpleNamespace(verdict="VERIFIED")

    def dispatch_stage(self, stage_name: str) -> None:
        if stage_name == "MANAGER":
            self.next_stage = "VERIFY"
            self.scratchpad.append("MANAGER")
            return
        if stage_name == "PERSONA":
            self.next_stage = "FINISHED"
            self.scratchpad.append("PERSONA")
            return
        raise RuntimeError(f"Unexpected stage: {stage_name}")


class InterruptDummyOrchestrator(BaseDummyOrchestrator):
    """Simulates a TASK route that pauses for approval: ROUTE -> MANAGER -> VERIFY -> AWAIT_INTERRUPT."""

    def __init__(self) -> None:
        super().__init__()
        self.pending_stage_pause = {
            "pause_type": "approval",
            "question": "Continue?",
        }

    def dispatch_stage(self, stage_name: str) -> None:
        if stage_name == "MANAGER":
            self.next_stage = "VERIFY"
            self.scratchpad.append("MANAGER")
            return
        if stage_name == "PERSONA":
            self.next_stage = "FINISHED"
            self.scratchpad.append("PERSONA")
            return
        raise RuntimeError(f"Unexpected stage: {stage_name}")


def _make_initial_state() -> PiperState:
    return PiperState(
        messages=[],
        stage="INIT",
        route_decision=None,
        manager_result=None,
        verification_passed=False,
        pre_persona_output=None,
        persona_output=None,
        workspace_path=".",
        interrupt_payload=None,
    )


def _run_chat_smoke() -> dict:
    dummy = ChatDummyOrchestrator()

    def _mock_route(orc):
        orc.route_decision = {"decision": "CHAT"}
        orc.next_stage = "PERSONA"
        orc.scratchpad.append("ROUTE")

    def _mock_persona(orc):
        orc.next_stage = "FINISHED"
        orc.scratchpad.append("PERSONA")

    with patch("core.orchestrator_phases._run_route_core", _mock_route), \
         patch("core.orchestrator_phases._run_persona_core", _mock_persona):
        graph = build_piper_graph()
        result = graph.invoke(
            _make_initial_state(),
            config={"configurable": {"thread_id": "chat-smoke", "orchestrator": dummy}},
        )

    return {
        "ok": bool(
            result.get("stage") == "PERSONA"
            and dummy.next_stage == "FINISHED"
            and dummy.scratchpad == ["ROUTE", "PERSONA"]
        ),
        "stage": result.get("stage"),
        "scratchpad": dummy.scratchpad,
    }


def _run_search_smoke() -> dict:
    dummy = SearchDummyOrchestrator()

    def _mock_route(orc):
        orc.route_decision = {"decision": "SEARCH", "card": {"query": "test"}}
        orc.next_stage = "SEARCH"
        orc.scratchpad.append("ROUTE")

    with patch("core.orchestrator_phases._run_route_core", _mock_route):
        graph = build_piper_graph()
        result = graph.invoke(
            _make_initial_state(),
            config={"configurable": {"thread_id": "search-smoke", "orchestrator": dummy}},
        )

    return {
        "ok": bool(
            result.get("stage") == "SEARCH"
            and dummy.next_stage == "FINISHED"
            and dummy.scratchpad == ["ROUTE", "SEARCH"]
        ),
        "stage": result.get("stage"),
        "scratchpad": dummy.scratchpad,
    }


def _run_explain_smoke() -> dict:
    dummy = ExplainDummyOrchestrator()

    def _mock_route(orc):
        orc.route_decision = {
            "decision": "CHAT",
            "interceptor": "EXPLAIN",
            "system_notice": {"kind": "explain_last_turn"},
        }
        orc.next_stage = "EXPLAIN"
        orc.scratchpad.append("ROUTE")

    def _mock_persona(orc):
        orc.next_stage = "FINISHED"
        orc.scratchpad.append("PERSONA")

    with patch("core.orchestrator_phases._run_route_core", _mock_route), \
         patch("core.orchestrator_phases._run_persona_core", _mock_persona):
        graph = build_piper_graph()
        result = graph.invoke(
            _make_initial_state(),
            config={"configurable": {"thread_id": "explain-smoke", "orchestrator": dummy}},
        )

    return {
        "ok": bool(
            result.get("stage") == "PERSONA"
            and dummy.next_stage == "FINISHED"
            and dummy.scratchpad == ["ROUTE", "EXPLAIN", "PERSONA"]
        ),
        "stage": result.get("stage"),
        "scratchpad": dummy.scratchpad,
    }


def _run_task_smoke() -> dict:
    dummy = TaskDummyOrchestrator()

    def _mock_route(orc):
        orc.route_decision = {"decision": "TASK", "card": {"goal": "Do work"}}
        orc.next_stage = "MANAGER"
        orc.scratchpad.append("ROUTE")

    def _mock_manager(orc):
        orc.next_stage = "VERIFY"
        orc.scratchpad.append("MANAGER")

    def _mock_persona(orc):
        orc.next_stage = "FINISHED"
        orc.scratchpad.append("PERSONA")

    with patch("core.orchestrator_phases._run_route_core", _mock_route), \
         patch("core.orchestrator_phases._run_manager_core", _mock_manager), \
         patch("core.orchestrator_phases._run_persona_core", _mock_persona):
        graph = build_piper_graph()
        result = graph.invoke(
            _make_initial_state(),
            config={"configurable": {"thread_id": "task-smoke", "orchestrator": dummy}},
        )

    return {
        "ok": bool(
            result.get("stage") == "PERSONA"
            and dummy.next_stage == "FINISHED"
            and dummy.scratchpad == ["ROUTE", "MANAGER", "PERSONA"]
        ),
        "stage": result.get("stage"),
        "scratchpad": dummy.scratchpad,
    }


def _run_interrupt_smoke() -> dict:
    dummy = InterruptDummyOrchestrator()

    def _mock_route(orc):
        orc.route_decision = {"decision": "TASK", "card": {"goal": "Do work"}}
        orc.next_stage = "MANAGER"
        orc.scratchpad.append("ROUTE")

    def _mock_manager(orc):
        orc.next_stage = "VERIFY"
        orc.scratchpad.append("MANAGER")

    def _mock_interrupt(payload):
        return {"user_msg": "yes"}

    def _mock_persona(orc):
        orc.next_stage = "FINISHED"
        orc.scratchpad.append("PERSONA")

    with patch("core.orchestrator_phases._run_route_core", _mock_route), \
         patch("core.orchestrator_phases._run_manager_core", _mock_manager), \
         patch("core.orchestrator_phases._run_persona_core", _mock_persona), \
         patch("langgraph.types.interrupt", _mock_interrupt):
        graph = build_piper_graph()
        result = graph.invoke(
            _make_initial_state(),
            config={"configurable": {"thread_id": "interrupt-smoke", "orchestrator": dummy}},
        )

    return {
        "ok": bool(
            result.get("stage") == "PERSONA"
            and dummy.next_stage == "FINISHED"
            and "LANGGRAPH_INTERRUPT_RESUME" in " ".join(dummy.scratchpad)
        ),
        "stage": result.get("stage"),
        "scratchpad": dummy.scratchpad,
    }


def main() -> int:
    graph_available = True
    graph_error = ""
    try:
        build_piper_graph()
    except RuntimeError as exc:
        graph_available = False
        graph_error = str(exc)

    tests = {
        "chat_smoke": {"ok": False, "error": "graph unavailable"},
        "search_smoke": {"ok": False, "error": "graph unavailable"},
        "explain_smoke": {"ok": False, "error": "graph unavailable"},
        "task_smoke": {"ok": False, "error": "graph unavailable"},
        "interrupt_smoke": {"ok": False, "error": "graph unavailable"},
    }

    if graph_available:
        for name, runner in [
            ("chat_smoke", _run_chat_smoke),
            ("search_smoke", _run_search_smoke),
            ("explain_smoke", _run_explain_smoke),
            ("task_smoke", _run_task_smoke),
            ("interrupt_smoke", _run_interrupt_smoke),
        ]:
            try:
                tests[name] = runner()
            except Exception as exc:
                tests[name] = {"ok": False, "error": str(exc)}

    success = bool(
        graph_available
        and all(v.get("ok") for v in tests.values())
    )

    report = {
        "success": success,
        "graph_available": bool(graph_available),
        "graph_error": graph_error,
        **tests,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
